"""FTN FAAB recommendations Prefect flow (ticket #46).

Downloads Vlad's 12- and 15-team FAAB CSV exports and uploads to:
    s3://dn-lakehouse-dev/ftn/faab/year=/month=/day=/<filename>

Filenames match manual exports in ``data/ftn/faab/`` (e.g.
``12 Team FAAB 2026.csv``, ``15 team faab 2026.csv``).

Auth uses FTN JWT cookies from AWS Secrets Manager:
    ftn_refresh_token, ftn_access_token, ftn_user_id

FTN projections are tracked in #122. dbt Cloud job trigger is deferred
until a production job exists.

Run locally without AWS or a Prefect API:
    python flows/ftn_faab.py --dry-run

Run locally for real (needs AWS creds for Secrets Manager + S3 PutObject):
    python flows/ftn_faab.py
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from curl_cffi import requests as curl_requests
from prefect import flow, get_run_logger, task

_FLOWS_DIR = Path(__file__).resolve().parent
if str(_FLOWS_DIR) not in sys.path:
    sys.path.insert(0, str(_FLOWS_DIR))

from hello_flow import _parse_s3_uri, _s3_client  # noqa: E402

DEFAULT_S3_BASE = "s3://dn-lakehouse-dev/ftn/faab"
DEFAULT_SECRET_NAME = "fantasy-baseball-platform"
DEFAULT_SECRET_REGION = "us-east-1"
DEFAULT_REFRESH_TOKEN_KEY = "ftn_refresh_token"
DEFAULT_ACCESS_TOKEN_KEY = "ftn_access_token"
DEFAULT_USER_ID_KEY = "ftn_user_id"
DEFAULT_API_BASE = "https://api.ftnfantasy.com"
DEFAULT_SEASON_YEAR = "2026"
BROWSER_IMPERSONATE = "chrome120"
DOWNLOAD_TIMEOUT_SECONDS = 120
PARTITION_TZ = ZoneInfo("America/New_York")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

EXPECTED_CSV_HEADER = (
    "Player",
    "Position",
    "Team",
    "Own%",
    "Type",
    "Low Bid",
    "High Bid",
    "Notes / SP Matchups",
)


class FtnAuthError(Exception):
    """Raised when FTN rejects the session tokens."""


class FtnDownloadError(Exception):
    """Raised when a single FAAB download or upload fails."""


@dataclass(frozen=True)
class FaabTarget:
    page_url: str
    filename: str
    wpdatatable_id: str


FAAB_TARGETS: tuple[FaabTarget, ...] = (
    FaabTarget(
        page_url="https://ftnfantasy.com/fantasy/mlb/12-team-faab",
        filename=f"12 Team FAAB {DEFAULT_SEASON_YEAR}.csv",
        wpdatatable_id="151",
    ),
    FaabTarget(
        page_url="https://ftnfantasy.com/fantasy/mlb/15-team-faab",
        filename=f"15 team faab {DEFAULT_SEASON_YEAR}.csv",
        wpdatatable_id="152",
    ),
)


@dataclass(frozen=True)
class FtnTokens:
    access_token: str
    refresh_token: str
    user_id: str


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


def fetch_ftn_tokens(
    *,
    secret_name: str,
    secret_region: str,
    refresh_token_key: str,
    access_token_key: str,
    user_id_key: str,
    aws_credentials_block: str | None = None,
) -> FtnTokens:
    payload = fetch_secret_json(
        secret_name, region=secret_region, aws_credentials_block=aws_credentials_block
    )
    missing = [
        key
        for key, name in (
            (refresh_token_key, "ftn_refresh_token"),
            (access_token_key, "ftn_access_token"),
            (user_id_key, "ftn_user_id"),
        )
        if not str(payload.get(key) or "").strip()
    ]
    if missing:
        raise ValueError(
            f"Secret {secret_name} is missing FTN auth keys: {', '.join(missing)}"
        )
    return FtnTokens(
        access_token=str(payload[access_token_key]).strip(),
        refresh_token=str(payload[refresh_token_key]).strip(),
        user_id=str(payload[user_id_key]).strip(),
    )


def _jwt_exp_unix(token: str) -> int | None:
    try:
        payload_segment = token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (IndexError, json.JSONDecodeError, ValueError):
        return None
    exp = payload.get("exp")
    return int(exp) if exp is not None else None


def is_access_token_expired(access_token: str, *, skew_seconds: int = 60) -> bool:
    exp = _jwt_exp_unix(access_token)
    if exp is None:
        return True
    now = int(datetime.now(timezone.utc).timestamp())
    return now >= (exp - skew_seconds)


def refresh_access_token(
    tokens: FtnTokens,
    *,
    api_base: str = DEFAULT_API_BASE,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> FtnTokens:
    response = requests.post(
        f"{api_base.rstrip('/')}/users/token/refresh",
        json={
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
        },
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        raise FtnAuthError(
            "FTN token refresh failed "
            f"(HTTP {response.status_code}: {response.text[:200]})"
        )
    payload = response.json()
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or tokens.refresh_token).strip()
    user_id = str(payload.get("user_id") or tokens.user_id).strip()
    if not access_token:
        raise FtnAuthError("FTN token refresh response missing access_token")
    return FtnTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user_id,
    )


def resolve_access_token(
    tokens: FtnTokens,
    *,
    api_base: str = DEFAULT_API_BASE,
) -> FtnTokens:
    """Return a usable access token, refreshing when the JWT is near expiry.

    FTN's refresh endpoint can return HTTP 500 when the refresh_token in
    Secrets Manager is stale, even though the cookie values still work for
    page fetches. On refresh failure we keep the stored tokens and let the
    download step surface a real auth error if the session is dead.
    """
    if not is_access_token_expired(tokens.access_token):
        return tokens
    try:
        return refresh_access_token(tokens, api_base=api_base)
    except FtnAuthError as exc:
        logging.getLogger(__name__).warning(
            "FTN access_token JWT is expired and refresh failed (%s); "
            "continuing with stored cookies",
            exc,
        )
        return tokens


def build_cookie_header(tokens: FtnTokens) -> str:
    return (
        f"refresh_token={tokens.refresh_token}; "
        f"access_token={tokens.access_token}; "
        f"user_id={tokens.user_id}"
    )


def _clean_table_cell(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = unescape(text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    return text.strip()


def _parse_wpdatatable_rows(html: str, wpdatatable_id: str) -> list[list[str]]:
    table_match = re.search(
        rf"<table[^>]+wpDataTableID-{re.escape(wpdatatable_id)}[^>]*>(.*?)</table>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        raise FtnDownloadError(
            f"wpDataTableID-{wpdatatable_id} not found in FTN page HTML"
        )

    rows: list[list[str]] = []
    for row_html in re.findall(
        r"<tr[^>]*>(.*?)</tr>", table_match.group(1), re.IGNORECASE | re.DOTALL
    ):
        cells = [
            _clean_table_cell(cell_html)
            for cell_html in re.findall(
                r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.IGNORECASE | re.DOTALL
            )
        ]
        if not cells:
            continue
        if not cells[0] and tuple(cells) != EXPECTED_CSV_HEADER:
            continue
        rows.append(cells)

    if not rows:
        raise FtnDownloadError(
            f"wpDataTableID-{wpdatatable_id} table had no parseable rows"
        )
    if tuple(rows[0]) != EXPECTED_CSV_HEADER:
        raise FtnDownloadError(
            f"wpDataTableID-{wpdatatable_id} header mismatch: {rows[0]!r}"
        )
    return rows


def _rows_to_csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(
        buffer,
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def validate_faab_csv(body: bytes, *, filename: str) -> None:
    if not body:
        raise FtnDownloadError(f"FTN returned an empty CSV for {filename}")

    text = body.decode("utf-8-sig", errors="replace").lstrip()
    lowered = text.lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html"):
        if "human verification" in lowered:
            raise FtnDownloadError(
                f"FTN bot protection blocked the FAAB page fetch for {filename}"
            )
        raise FtnAuthError(
            f"FTN returned HTML instead of CSV for {filename} (session likely expired)"
        )

    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        raise FtnDownloadError(f"FTN CSV for {filename} has no header row")
    normalized = tuple(col.strip().strip('"') for col in header)
    if normalized != EXPECTED_CSV_HEADER:
        raise FtnDownloadError(
            f"FTN CSV header mismatch for {filename}: got {normalized!r}"
        )

    data_rows = list(reader)
    if not data_rows:
        raise FtnDownloadError(f"FTN CSV for {filename} has no data rows")


def fetch_faab_page_html(
    target: FaabTarget,
    tokens: FtnTokens,
    *,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> str:
    response = curl_requests.get(
        target.page_url,
        headers={
            "Cookie": build_cookie_header(tokens),
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        impersonate=BROWSER_IMPERSONATE,
        timeout=timeout_seconds,
    )
    if response.status_code in (401, 403):
        raise FtnAuthError(
            f"FTN auth rejected while fetching {target.page_url} "
            f"(HTTP {response.status_code})"
        )
    if response.status_code != 200:
        raise FtnDownloadError(
            f"FTN HTTP {response.status_code} for {target.page_url}"
        )

    html = response.text
    if "Human Verification" in html:
        raise FtnDownloadError(
            f"FTN bot protection blocked the FAAB page fetch for {target.filename}"
        )
    marker = f"wpDataTableID-{target.wpdatatable_id}"
    if marker not in html:
        if "complex-paywall" in html or "Subscribe to access" in html:
            raise FtnAuthError(
                f"FTN FAAB table missing for {target.filename} (subscription/auth issue)"
            )
        raise FtnDownloadError(
            f"FTN FAAB table marker {marker!r} missing from {target.page_url}"
        )
    return html


def download_faab_csv(
    target: FaabTarget,
    tokens: FtnTokens,
    *,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    html = fetch_faab_page_html(target, tokens, timeout_seconds=timeout_seconds)
    rows = _parse_wpdatatable_rows(html, target.wpdatatable_id)
    body = _rows_to_csv_bytes(rows)
    validate_faab_csv(body, filename=target.filename)
    return body


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
def ingest_faab_file(
    target: FaabTarget,
    *,
    tokens: FtnTokens,
    bucket: str,
    base_prefix: str,
    stamp: datetime,
    aws_credentials_block: str | None,
    dry_run: bool,
) -> str:
    logger = get_run_logger()
    key = build_csv_s3_key(base_prefix, stamp, target.filename)
    uri = f"s3://{bucket}/{key}"

    if dry_run:
        logger.info(
            "DRY RUN — would download %s and upload %s",
            target.page_url,
            uri,
        )
        return uri

    body = download_faab_csv(target, tokens)
    uri = put_csv_object(bucket, key, body, aws_credentials_block)
    logger.info("Uploaded %s (%s bytes)", uri, len(body))
    return uri


@flow(name="ftn-faab")
def ftn_faab(
    s3_base_path: str = DEFAULT_S3_BASE,
    secret_name: str = DEFAULT_SECRET_NAME,
    secret_region: str = DEFAULT_SECRET_REGION,
    refresh_token_key: str = DEFAULT_REFRESH_TOKEN_KEY,
    access_token_key: str = DEFAULT_ACCESS_TOKEN_KEY,
    user_id_key: str = DEFAULT_USER_ID_KEY,
    aws_credentials_block: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Download FTN FAAB CSVs for 12- and 15-team leagues."""
    logger = get_run_logger()
    stamp = partition_stamp()
    bucket, base_prefix = _parse_s3_uri(s3_base_path)

    tokens = FtnTokens(
        access_token="dry-run-access",
        refresh_token="dry-run-refresh",
        user_id="0",
    )
    if not dry_run:
        tokens = fetch_ftn_tokens(
            secret_name=secret_name,
            secret_region=secret_region,
            refresh_token_key=refresh_token_key,
            access_token_key=access_token_key,
            user_id_key=user_id_key,
            aws_credentials_block=aws_credentials_block,
        )
        tokens = resolve_access_token(tokens)

    successes: dict[str, str] = {}
    failures: dict[str, str] = {}

    for target in FAAB_TARGETS:
        try:
            uri = ingest_faab_file(
                target,
                tokens=tokens,
                bucket=bucket,
                base_prefix=base_prefix,
                stamp=stamp,
                aws_credentials_block=aws_credentials_block,
                dry_run=dry_run,
            )
            successes[target.filename] = uri
        except FtnAuthError:
            logger.exception("FTN auth failed for %s", target.filename)
            raise
        except Exception as exc:
            message = str(exc)
            failures[target.filename] = message
            logger.error("FAAB file %s failed: %s", target.filename, message)

    summary = {"successes": successes, "failures": failures}
    logger.info("FTN FAAB ingest complete: %s", summary)

    if failures:
        failed = ", ".join(f"{name} ({reason})" for name, reason in failures.items())
        raise FtnDownloadError(
            f"{len(failures)} FAAB file(s) failed after others succeeded: {failed}"
        )

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the FTN FAAB Prefect flow.")
    parser.add_argument(
        "--s3-path",
        default=DEFAULT_S3_BASE,
        help=f"Base S3 URI (default: {DEFAULT_S3_BASE})",
    )
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
        help="Name of a Prefect AwsCredentials block (for Prefect Managed compute).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned downloads/uploads instead of calling FTN/AWS.",
    )
    args = parser.parse_args()
    print(
        ftn_faab(
            s3_base_path=args.s3_path,
            secret_name=args.secret_name,
            secret_region=args.secret_region,
            aws_credentials_block=args.aws_credentials_block,
            dry_run=args.dry_run,
        )
    )
