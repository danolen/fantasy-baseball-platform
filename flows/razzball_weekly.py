"""Razzball weekly + weekend projections Prefect flow (ticket #47).

Subscriber pages expose a client-side "Get CSV" button (TableSorter output
widget). There is no server CSV URL — the flow fetches each page HTML and
parses table ``#neorazzstatstable`` into CSV (same data as the button).

Uploads:
    s3://dn-lakehouse-dev/razzball/projections/weekly/hitting/hittertron.csv
    s3://dn-lakehouse-dev/razzball/projections/weekly/pitching/streamonator.csv
    s3://dn-lakehouse-dev/razzball/projections/weekly/weekend_hitting/hittertron.csv

Auth uses the Razzball WordPress session from AWS Secrets Manager
(``razzball_cookie`` key — paste ``wordpress_logged_in_...`` and
``wordpress_sec_...`` name=value pairs from DevTools).

dbt Cloud job trigger is deferred until a production job exists.

Run locally without AWS or a Prefect API:
    python flows/razzball_weekly.py --dry-run

Run locally for real (needs AWS creds for Secrets Manager + S3 PutObject):
    python flows/razzball_weekly.py
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from curl_cffi import requests as curl_requests
from prefect import flow, get_run_logger, task

_FLOWS_DIR = Path(__file__).resolve().parent
if str(_FLOWS_DIR) not in sys.path:
    sys.path.insert(0, str(_FLOWS_DIR))

from hello_flow import _parse_s3_uri, _s3_client  # noqa: E402

DEFAULT_S3_WEEKLY_HITTING = (
    "s3://dn-lakehouse-dev/razzball/projections/weekly/hitting"
)
DEFAULT_S3_WEEKLY_PITCHING = (
    "s3://dn-lakehouse-dev/razzball/projections/weekly/pitching"
)
DEFAULT_S3_WEEKEND_HITTING = (
    "s3://dn-lakehouse-dev/razzball/projections/weekly/weekend_hitting"
)
DEFAULT_SECRET_NAME = "fantasy-baseball-platform"
DEFAULT_SECRET_REGION = "us-east-1"
DEFAULT_RAZZBALL_COOKIE_KEY = "razzball_cookie"
TABLE_ID = "neorazzstatstable"
BROWSER_IMPERSONATE = "chrome120"
DOWNLOAD_TIMEOUT_SECONDS = 120
PARTITION_TZ = ZoneInfo("America/New_York")

# Headers from manual exports in data/razzball/projections/weekly/.
EXPECTED_WEEKLY_HITTING_HEADER = (
    "#",
    "Name",
    "B",
    "Team",
    "Wk of",
    "Opps",
    "#G",
    "HG",
    "AG",
    "vR",
    "vL",
    "ESPN",
    "Y!",
    "$",
    "$/G",
    "$ MT",
    "$ FS",
    "G",
    "PA",
    "AB",
    "H",
    "R",
    "HR",
    "RBI",
    "SB",
    "BB",
    "SO",
    "AVG",
    "OBP",
    "SLG",
    "OPS",
    "R%",
    "ROS12 $/G",
    "RFS12",
    "RFS15",
    "RazzID",
    "NFBCID",
)
EXPECTED_WEEKLY_PITCHING_HEADER = (
    "#",
    "Name",
    "Team",
    "Pos",
    "Week of",
    "Opp",
    "$",
    "$/G",
    "G",
    "GS",
    "QS",
    "W",
    "L",
    "SV",
    "HLD",
    "IP",
    "H",
    "ER",
    "K",
    "BB",
    "HR",
    "ERA",
    "WHIP",
    "R%",
    "ROS12 $/G",
    "RFS12",
    "RFS15",
    "Next Proj Opps",
    "RazzID",
    "NFBCID",
)
EXPECTED_WEEKEND_HITTING_HEADER = (
    "#",
    "Name",
    "B",
    "Team",
    "Fri",
    "FRI ST%",
    "Opp",
    "SP",
    "#G",
    "HG",
    "AG",
    "vR",
    "vL",
    "ESPN",
    "Y!",
    "$",
    "G",
    "PA",
    "AB",
    "H",
    "R",
    "HR",
    "RBI",
    "SB",
    "BB",
    "SO",
    "AVG",
    "OBP",
    "SLG",
    "OPS",
    "R%",
    "RazzID",
    "NFBCID",
)


class RazzballAuthError(Exception):
    """Raised when Razzball rejects the session cookie (paywall, expired login)."""


class RazzballDownloadError(Exception):
    """Raised when a single projection download or upload fails."""


@dataclass(frozen=True)
class ProjectionTarget:
    slug: str
    page_url: str
    filename: str
    s3_base_path: str
    expected_header: tuple[str, ...]


PROJECTION_TARGETS: dict[str, ProjectionTarget] = {
    "weekly_hitting": ProjectionTarget(
        slug="weekly_hitting",
        page_url="https://razzball.com/hittertron-nextweek/",
        filename="hittertron.csv",
        s3_base_path=DEFAULT_S3_WEEKLY_HITTING,
        expected_header=EXPECTED_WEEKLY_HITTING_HEADER,
    ),
    "weekly_pitching": ProjectionTarget(
        slug="weekly_pitching",
        page_url="https://razzball.com/streamers-nextweek/",
        filename="streamonator.csv",
        s3_base_path=DEFAULT_S3_WEEKLY_PITCHING,
        expected_header=EXPECTED_WEEKLY_PITCHING_HEADER,
    ),
    "weekend_hitting": ProjectionTarget(
        slug="weekend_hitting",
        page_url="https://razzball.com/hittertron-nextfriday-sunday/",
        filename="hittertron.csv",
        s3_base_path=DEFAULT_S3_WEEKEND_HITTING,
        expected_header=EXPECTED_WEEKEND_HITTING_HEADER,
    ),
}


def partition_stamp(tz: ZoneInfo = PARTITION_TZ) -> datetime:
    return datetime.now(tz)


def build_csv_s3_key(base_prefix: str, stamp: datetime, filename: str) -> str:
    partition = f"year={stamp.year}/month={stamp.month:02d}/day={stamp.day:02d}"
    return f"{base_prefix}/{partition}/{filename}" if base_prefix else f"{partition}/{filename}"


def _boto3_session(aws_credentials_block: str | None):
    if aws_credentials_block:
        from prefect_aws import AwsCredentials

        return AwsCredentials.load(aws_credentials_block).get_boto3_session()

    import boto3

    return boto3.Session()


def fetch_secret_json(
    secret_name: str,
    *,
    region: str,
    aws_credentials_block: str | None = None,
) -> dict:
    client = _boto3_session(aws_credentials_block).client(
        "secretsmanager", region_name=region
    )
    response = client.get_secret_value(SecretId=secret_name)
    raw = response.get("SecretString")
    if not raw:
        raise ValueError(f"Secret {secret_name} has no SecretString payload")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"Secret {secret_name} must be a JSON object")
    return payload


def fetch_razzball_cookie(
    *,
    secret_name: str,
    secret_region: str,
    secret_key: str = DEFAULT_RAZZBALL_COOKIE_KEY,
    aws_credentials_block: str | None = None,
) -> str:
    payload = fetch_secret_json(
        secret_name, region=secret_region, aws_credentials_block=aws_credentials_block
    )
    raw = payload.get(secret_key)
    if not raw or not str(raw).strip():
        raise ValueError(
            f"Secret {secret_name} is missing key {secret_key!r} for Razzball auth"
        )
    cookie = str(raw).strip()
    if "wordpress_logged_in" not in cookie or "wordpress_sec" not in cookie:
        raise ValueError(
            f"Secret {secret_name} key {secret_key!r} must include "
            "wordpress_logged_in_... and wordpress_sec_... cookies"
        )
    return cookie


def fetch_projection_html(
    target: ProjectionTarget,
    *,
    cookie: str,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> str:
    response = curl_requests.get(
        target.page_url,
        headers={"Cookie": cookie},
        impersonate=BROWSER_IMPERSONATE,
        timeout=timeout_seconds,
    )
    if response.status_code in (401, 403):
        raise RazzballAuthError(
            f"Razzball auth rejected for {target.page_url} (HTTP {response.status_code})"
        )
    if response.status_code != 200:
        raise RazzballDownloadError(
            f"Razzball HTTP {response.status_code} for {target.page_url}"
        )

    text = response.text
    if "Membership Required" in text and TABLE_ID not in text:
        raise RazzballAuthError(
            f"Razzball paywall for {target.page_url} (session cookie likely expired)"
        )
    return text


def _extract_best_table_html(html: str) -> str:
    """Return the largest #neorazzstatstable block from page HTML.

    Pitching pages embed multiple tables with the same id (one populated stats
    table plus empty shells). Weekend/hitting pages can also use malformed
    THEAD/TBODY markup that breaks full-page BeautifulSoup parsing — isolating
    the biggest table block avoids both issues.
    """
    blocks = re.findall(
        rf'<table[^>]*id="{TABLE_ID}"[^>]*>.*?</table>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not blocks:
        return html
    return max(blocks, key=lambda block: block.lower().count("<td"))


def html_table_to_csv(html: str, *, expected_header: tuple[str, ...]) -> bytes:
    """Parse #neorazzstatstable into CSV bytes (UTF-8 with BOM)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_extract_best_table_html(html), "html.parser")
    table = soup.find("table", id=TABLE_ID)
    if table is None:
        raise RazzballAuthError(
            f"Razzball table #{TABLE_ID} not found "
            "(session cookie likely expired or page layout changed)"
        )

    rows: list[list[str]] = []
    thead = table.find("thead")
    if thead:
        for tr in thead.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) > 1:
                rows.append([cell.get_text(strip=True) for cell in cells])
                break

    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) <= 1:
            continue
        row = [cell.get_text(strip=True) for cell in cells]
        if row[0] == "#" or row[0].lower() == "name":
            continue
        rows.append(row)

    if len(rows) < 2:
        raise RazzballDownloadError(
            f"Razzball table #{TABLE_ID} had no data rows after parsing"
        )

    header = rows[0]
    if tuple(header) != expected_header:
        raise RazzballDownloadError(
            f"Razzball CSV header mismatch: expected {len(expected_header)} columns, "
            f"got {len(header)}; first diff at col 0: {header[:3]!r} vs "
            f"{expected_header[:3]!r}"
        )

    data_rows = rows[1:]
    for index, row in enumerate(data_rows, start=1):
        row[0] = str(index)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(data_rows)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def validate_projection_csv(body: bytes, *, expected_header: tuple[str, ...]) -> None:
    text = body.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None:
        raise RazzballDownloadError("Razzball CSV has no header row")
    if tuple(header) != expected_header:
        raise RazzballDownloadError(
            f"Razzball CSV header validation failed: {header[:5]!r}"
        )
    data_rows = list(reader)
    if not data_rows:
        raise RazzballDownloadError("Razzball CSV has no data rows")


@task
def put_csv_object(
    bucket: str,
    key: str,
    body: bytes,
    aws_credentials_block: str | None = None,
) -> str:
    _s3_client(aws_credentials_block).put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="text/csv",
    )
    return f"s3://{bucket}/{key}"


@task
def ingest_projection(
    target: ProjectionTarget,
    *,
    cookie: str,
    stamp: datetime,
    aws_credentials_block: str | None = None,
    dry_run: bool = False,
) -> str:
    logger = get_run_logger()
    bucket, base_prefix = _parse_s3_uri(target.s3_base_path)
    key = build_csv_s3_key(base_prefix, stamp, target.filename)
    uri = f"s3://{bucket}/{key}"

    if dry_run:
        logger.info(
            "DRY RUN — would fetch %s and upload %s",
            target.page_url,
            uri,
        )
        return uri

    html = fetch_projection_html(target, cookie=cookie)
    body = html_table_to_csv(html, expected_header=target.expected_header)
    validate_projection_csv(body, expected_header=target.expected_header)
    uri = put_csv_object(bucket, key, body, aws_credentials_block)
    logger.info("Uploaded %s (%s bytes, %s rows)", uri, len(body), body.count(b"\n"))
    return uri


@flow(name="razzball-weekly")
def razzball_weekly(
    secret_name: str = DEFAULT_SECRET_NAME,
    secret_region: str = DEFAULT_SECRET_REGION,
    secret_key: str = DEFAULT_RAZZBALL_COOKIE_KEY,
    include_weekly_hitting: bool = True,
    include_weekly_pitching: bool = True,
    include_weekend_hitting: bool = True,
    aws_credentials_block: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Download Razzball weekly/weekend projection CSVs and upload to S3."""
    logger = get_run_logger()
    stamp = partition_stamp()

    cookie = "dry-run-cookie"
    if not dry_run:
        cookie = fetch_razzball_cookie(
            secret_name=secret_name,
            secret_region=secret_region,
            secret_key=secret_key,
            aws_credentials_block=aws_credentials_block,
        )

    slice_flags = {
        "weekly_hitting": include_weekly_hitting,
        "weekly_pitching": include_weekly_pitching,
        "weekend_hitting": include_weekend_hitting,
    }
    if not any(slice_flags.values()):
        raise ValueError("At least one projection slice must be enabled")

    successes: dict[str, str] = {}
    failures: dict[str, str] = {}

    for slug, enabled in slice_flags.items():
        if not enabled:
            continue
        target = PROJECTION_TARGETS[slug]
        try:
            successes[slug] = ingest_projection(
                target,
                cookie=cookie,
                stamp=stamp,
                aws_credentials_block=aws_credentials_block,
                dry_run=dry_run,
            )
        except RazzballAuthError:
            logger.exception("Razzball auth failed for %s", slug)
            raise
        except Exception as exc:
            message = str(exc)
            failures[slug] = message
            logger.error("%s failed: %s", slug, message)

    summary = {"successes": successes, "failures": failures}
    logger.info("Razzball weekly ingest complete: %s", summary)

    if failures:
        failed = ", ".join(f"{name} ({reason})" for name, reason in failures.items())
        raise RazzballDownloadError(
            f"{len(failures)} projection(s) failed after others succeeded: {failed}"
        )

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Razzball weekly Prefect flow.")
    parser.add_argument(
        "--secret-name",
        default=DEFAULT_SECRET_NAME,
        help=f"AWS Secrets Manager secret name (default: {DEFAULT_SECRET_NAME})",
    )
    parser.add_argument(
        "--secret-region",
        default=DEFAULT_SECRET_REGION,
        help=f"AWS region for the secret (default: {DEFAULT_SECRET_REGION})",
    )
    parser.add_argument(
        "--aws-credentials-block",
        default=None,
        help="Prefect AwsCredentials block name (optional locally)",
    )
    parser.add_argument(
        "--weekly-hitting-only",
        action="store_true",
        help="Fetch only weekly hitting (hittertron-nextweek).",
    )
    parser.add_argument(
        "--weekly-pitching-only",
        action="store_true",
        help="Fetch only weekly pitching (streamers-nextweek).",
    )
    parser.add_argument(
        "--weekend-hitting-only",
        action="store_true",
        help="Fetch only weekend hitting (hittertron-nextfriday-sunday).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned downloads/uploads instead of calling Razzball/AWS.",
    )
    args = parser.parse_args()

    only_flags = (
        args.weekly_hitting_only,
        args.weekly_pitching_only,
        args.weekend_hitting_only,
    )
    if sum(only_flags) > 1:
        parser.error("Use at most one of --weekly-*-only / --weekend-hitting-only")

    if args.weekly_hitting_only:
        include_weekly_hitting, include_weekly_pitching, include_weekend_hitting = (
            True,
            False,
            False,
        )
    elif args.weekly_pitching_only:
        include_weekly_hitting, include_weekly_pitching, include_weekend_hitting = (
            False,
            True,
            False,
        )
    elif args.weekend_hitting_only:
        include_weekly_hitting, include_weekly_pitching, include_weekend_hitting = (
            False,
            False,
            True,
        )
    else:
        include_weekly_hitting = True
        include_weekly_pitching = True
        include_weekend_hitting = True

    print(
        razzball_weekly(
            secret_name=args.secret_name,
            secret_region=args.secret_region,
            include_weekly_hitting=include_weekly_hitting,
            include_weekly_pitching=include_weekly_pitching,
            include_weekend_hitting=include_weekend_hitting,
            aws_credentials_block=args.aws_credentials_block,
            dry_run=args.dry_run,
        )
    )
