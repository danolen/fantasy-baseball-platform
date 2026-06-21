"""FanGraphs rest-of-season projections Prefect flow (ticket #45).

Downloads ROS hitting and pitching projection CSVs via FanGraphs' internal
``/api/projections`` endpoint and uploads to:
    s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/hitting/
    s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/pitching/

Auth uses the FanGraphs WordPress session cookie from AWS Secrets Manager
(``fangraphs_cookie`` key). CSV headers match the manual Data Export layout in
``flows/templates/``.

dbt Cloud job trigger is deferred until a production job exists.

Run locally without AWS or a Prefect API:
    python flows/fangraphs_ros.py --dry-run

Run locally for real (needs AWS creds for Secrets Manager + S3 PutObject):
    python flows/fangraphs_ros.py
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from prefect import flow, get_run_logger, task

_FLOWS_DIR = Path(__file__).resolve().parent
if str(_FLOWS_DIR) not in sys.path:
    sys.path.insert(0, str(_FLOWS_DIR))

from hello_flow import _parse_s3_uri, _s3_client  # noqa: E402

DEFAULT_S3_HITTING_BASE = (
    "s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/hitting"
)
DEFAULT_S3_PITCHING_BASE = (
    "s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/pitching"
)
DEFAULT_SECRET_NAME = "fantasy-baseball-platform"
DEFAULT_SECRET_REGION = "us-east-1"
DEFAULT_FANGRAPHS_COOKIE_KEY = "fangraphs_cookie"
DEFAULT_API_BASE = "https://www.fangraphs.com/api/projections"
DOWNLOAD_TIMEOUT_SECONDS = 120
PARTITION_TZ = ZoneInfo("America/New_York")
HITTING_HEADER_PATH = _FLOWS_DIR / "templates" / "fangraphs_ros_hitting_header.csv"
PITCHING_HEADER_PATH = _FLOWS_DIR / "templates" / "fangraphs_ros_pitching_header.csv"


class FangraphsAuthError(Exception):
    """Raised when FanGraphs rejects the session cookie."""


class FangraphsDownloadError(Exception):
    """Raised when a single projection download or upload fails."""


@dataclass(frozen=True)
class ProjectionTarget:
    filename: str
    stats: str
    type_param: str
    s3_base_path: str


PROJECTIONS: tuple[ProjectionTarget, ...] = (
    ProjectionTarget("atc-hit.csv", "bat", "ratcdc", DEFAULT_S3_HITTING_BASE),
    ProjectionTarget("depthcharts-hit.csv", "bat", "rfangraphsdc", DEFAULT_S3_HITTING_BASE),
    ProjectionTarget("oopsy-hit.csv", "bat", "roopsydc", DEFAULT_S3_HITTING_BASE),
    ProjectionTarget("steamer-hit.csv", "bat", "steamerr", DEFAULT_S3_HITTING_BASE),
    ProjectionTarget("thebat-hit.csv", "bat", "rthebat", DEFAULT_S3_HITTING_BASE),
    ProjectionTarget("thebat-x-hit.csv", "bat", "rthebatx", DEFAULT_S3_HITTING_BASE),
    ProjectionTarget("zips-hit.csv", "bat", "rzips", DEFAULT_S3_HITTING_BASE),
    ProjectionTarget("atc-pitch.csv", "pit", "ratcdc", DEFAULT_S3_PITCHING_BASE),
    ProjectionTarget("depthcharts-pitch.csv", "pit", "rfangraphsdc", DEFAULT_S3_PITCHING_BASE),
    ProjectionTarget("oopsy-pitch.csv", "pit", "roopsydc", DEFAULT_S3_PITCHING_BASE),
    ProjectionTarget("steamer-pitch.csv", "pit", "steamerr", DEFAULT_S3_PITCHING_BASE),
    ProjectionTarget("thebat-pitch.csv", "pit", "rthebat", DEFAULT_S3_PITCHING_BASE),
    ProjectionTarget("thebat-x-pitch.csv", "pit", "rthebatx", DEFAULT_S3_PITCHING_BASE),
    ProjectionTarget("zips-pitch.csv", "pit", "rzips", DEFAULT_S3_PITCHING_BASE),
)

HITTING_DIRECT_MAP: dict[str, str] = {
    "Team": "Team",
    "G": "G",
    "PA": "PA",
    "AB": "AB",
    "H": "H",
    "1B": "1B",
    "2B": "2B",
    "3B": "3B",
    "HR": "HR",
    "R": "R",
    "RBI": "RBI",
    "BB": "BB",
    "IBB": "IBB",
    "SO": "SO",
    "HBP": "HBP",
    "SF": "SF",
    "SH": "SH",
    "GDP": "GDP",
    "SB": "SB",
    "CS": "CS",
    "AVG": "AVG",
    "BB%": "BB%",
    "K%": "K%",
    "BB/K": "BB/K",
    "OBP": "OBP",
    "SLG": "SLG",
    "wOBA": "wOBA",
    "OPS": "OPS",
    "ISO": "ISO",
    "Spd": "Spd",
    "BABIP": "BABIP",
    "UBR": "UBR",
    "wRC": "wRC",
    "wRAA": "wRAA",
    "wRC+": "wRC+",
    "Off": "Off",
    "Def": "Def",
    "WAR": "WAR",
    "ADP": "ADP",
    "FPTS": "FPTS",
    "SPTS": "SPTS",
}

PITCHING_DIRECT_MAP: dict[str, str] = {
    "Team": "Team",
    "W": "W",
    "L": "L",
    "ERA": "ERA",
    "G": "G",
    "GS": "GS",
    "SV": "SV",
    "HLD": "HLD",
    "BS": "BS",
    "IP": "IP",
    "TBF": "TBF",
    "H": "H",
    "R": "R",
    "ER": "ER",
    "HR": "HR",
    "BB": "BB",
    "IBB": "IBB",
    "HBP": "HBP",
    "SO": "SO",
    "K/9": "K/9",
    "BB/9": "BB/9",
    "K/BB": "K/BB",
    "HR/9": "HR/9",
    "K%": "K%",
    "BB%": "BB%",
    "K-BB%": "K-BB%",
    "AVG": "AVG",
    "WHIP": "WHIP",
    "BABIP": "BABIP",
    "LOB%": "LOB%",
    "GB%": "GB%",
    "FIP": "FIP",
    "WAR": "WAR",
    "RA9-WAR": "RA9-WAR",
    "ADP": "ADP",
    "FPTS": "FPTS",
    "SPTS": "SPTS",
}


def partition_stamp(tz: ZoneInfo = PARTITION_TZ) -> datetime:
    return datetime.now(tz)


def build_csv_s3_key(base_prefix: str, stamp: datetime, filename: str) -> str:
    partition = f"year={stamp.year}/month={stamp.month:02d}/day={stamp.day:02d}"
    return f"{base_prefix}/{partition}/{filename}" if base_prefix else f"{partition}/{filename}"


def load_header(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"CSV header template not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        row = next(reader)
    if not row:
        raise ValueError(f"CSV header template is empty: {path}")
    return row


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


def fetch_fangraphs_cookie(
    *,
    secret_name: str,
    secret_region: str,
    secret_key: str,
    aws_credentials_block: str | None = None,
) -> str:
    payload = fetch_secret_json(
        secret_name, region=secret_region, aws_credentials_block=aws_credentials_block
    )
    cookie = payload.get(secret_key)
    if not cookie or not str(cookie).strip():
        raise ValueError(
            f"Secret {secret_name} is missing key {secret_key!r} for FanGraphs auth"
        )
    return str(cookie).strip()


def _csv_value(value) -> str:
    if value is None:
        return ""
    return str(value)


def _apply_direct_map(out: dict[str, str], api_row: dict, direct_map: dict[str, str]) -> None:
    for csv_col, api_col in direct_map.items():
        if csv_col in out and api_col in api_row:
            out[csv_col] = _csv_value(api_row[api_col])


def hitting_csv_row(api_row: dict, fieldnames: list[str]) -> dict[str, str]:
    out = {name: "" for name in fieldnames}
    _apply_direct_map(out, api_row, HITTING_DIRECT_MAP)
    out["Name"] = _csv_value(api_row.get("PlayerName"))
    out["NameASCII"] = _csv_value(api_row.get("PlayerName"))
    out["PlayerId"] = _csv_value(api_row.get("playerid"))
    out["MLBAMID"] = _csv_value(api_row.get("xMLBAMID"))
    out["wSB"] = _csv_value(api_row.get("wBsR"))
    out["BsR"] = _csv_value(api_row.get("BaseRunning"))
    out["Fld"] = _csv_value(api_row.get("UZR"))
    out["FPTS/G"] = _csv_value(api_row.get("FPTS_G"))
    out["SPTS/G"] = _csv_value(api_row.get("SPTS_G"))
    return out


def pitching_csv_row(api_row: dict, fieldnames: list[str]) -> dict[str, str]:
    out = {name: "" for name in fieldnames}
    _apply_direct_map(out, api_row, PITCHING_DIRECT_MAP)
    out["Name"] = _csv_value(api_row.get("PlayerName"))
    out["NameASCII"] = _csv_value(api_row.get("PlayerName"))
    out["PlayerId"] = _csv_value(api_row.get("playerid"))
    out["MLBAMID"] = _csv_value(api_row.get("xMLBAMID"))
    out["FPTS/IP"] = _csv_value(api_row.get("FPTS_IP"))
    out["SPTS/IP"] = _csv_value(api_row.get("SPTS_IP"))
    return out


def rows_to_csv_bytes(fieldnames: list[str], rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def validate_projection_payload(rows: list[dict], *, filename: str) -> None:
    if not rows:
        raise FangraphsAuthError(
            f"FanGraphs returned no rows for {filename} (session may be expired)"
        )
    if "playerid" not in rows[0]:
        raise FangraphsAuthError(
            f"FanGraphs payload for {filename} is missing playerid "
            "(session cookie likely expired)"
        )


def download_projection_csv(
    target: ProjectionTarget,
    *,
    cookie: str,
    hitting_fieldnames: list[str],
    pitching_fieldnames: list[str],
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    response = requests.get(
        DEFAULT_API_BASE,
        params={"stats": target.stats, "type": target.type_param},
        headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"},
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        raise FangraphsDownloadError(
            f"FanGraphs HTTP {response.status_code} for {target.filename}"
        )

    text = response.text.lstrip()
    if text.lower().startswith("<!doctype") or text.lower().startswith("<html"):
        raise FangraphsAuthError(
            f"FanGraphs returned HTML for {target.filename} (session cookie likely expired)"
        )

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise FangraphsDownloadError(
            f"FanGraphs response for {target.filename} was not JSON"
        ) from exc

    if not isinstance(payload, list):
        raise FangraphsDownloadError(
            f"FanGraphs response for {target.filename} was not a JSON list"
        )

    validate_projection_payload(payload, filename=target.filename)

    if target.stats == "bat":
        csv_rows = [hitting_csv_row(row, hitting_fieldnames) for row in payload]
        fieldnames = hitting_fieldnames
    else:
        csv_rows = [pitching_csv_row(row, pitching_fieldnames) for row in payload]
        fieldnames = pitching_fieldnames

    return rows_to_csv_bytes(fieldnames, csv_rows)


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
    hitting_fieldnames: list[str],
    pitching_fieldnames: list[str],
    stamp: datetime,
    aws_credentials_block: str | None,
    dry_run: bool,
) -> str:
    logger = get_run_logger()
    bucket, base_prefix = _parse_s3_uri(target.s3_base_path)
    key = build_csv_s3_key(base_prefix, stamp, target.filename)
    uri = f"s3://{bucket}/{key}"

    if dry_run:
        logger.info(
            "DRY RUN — would download stats=%s type=%s and upload %s",
            target.stats,
            target.type_param,
            uri,
        )
        return uri

    body = download_projection_csv(
        target,
        cookie=cookie,
        hitting_fieldnames=hitting_fieldnames,
        pitching_fieldnames=pitching_fieldnames,
    )
    uri = put_csv_object(bucket, key, body, aws_credentials_block)
    logger.info("Uploaded %s (%s bytes)", uri, len(body))
    return uri


@flow(name="fangraphs-ros")
def fangraphs_ros(
    secret_name: str = DEFAULT_SECRET_NAME,
    secret_region: str = DEFAULT_SECRET_REGION,
    secret_key: str = DEFAULT_FANGRAPHS_COOKIE_KEY,
    aws_credentials_block: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Download FanGraphs ROS projection CSVs for all configured systems."""
    logger = get_run_logger()
    stamp = partition_stamp()
    hitting_fieldnames = load_header(HITTING_HEADER_PATH)
    pitching_fieldnames = load_header(PITCHING_HEADER_PATH)

    cookie = "dry-run-cookie"
    if not dry_run:
        cookie = fetch_fangraphs_cookie(
            secret_name=secret_name,
            secret_region=secret_region,
            secret_key=secret_key,
            aws_credentials_block=aws_credentials_block,
        )

    successes: dict[str, str] = {}
    failures: dict[str, str] = {}

    for target in PROJECTIONS:
        try:
            uri = ingest_projection(
                target,
                cookie=cookie,
                hitting_fieldnames=hitting_fieldnames,
                pitching_fieldnames=pitching_fieldnames,
                stamp=stamp,
                aws_credentials_block=aws_credentials_block,
                dry_run=dry_run,
            )
            successes[target.filename] = uri
        except FangraphsAuthError:
            logger.exception("FanGraphs auth failed for %s", target.filename)
            raise
        except Exception as exc:
            message = str(exc)
            failures[target.filename] = message
            logger.error("Projection %s failed: %s", target.filename, message)

    summary = {"successes": successes, "failures": failures}
    logger.info("FanGraphs ROS ingest complete: %s", summary)

    if failures:
        failed = ", ".join(f"{name} ({reason})" for name, reason in failures.items())
        raise FangraphsDownloadError(
            f"{len(failures)} projection(s) failed after others succeeded: {failed}"
        )

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the FanGraphs ROS Prefect flow.")
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
        help="Print planned downloads/uploads instead of calling FanGraphs/AWS.",
    )
    args = parser.parse_args()
    print(
        fangraphs_ros(
            secret_name=args.secret_name,
            secret_region=args.secret_region,
            aws_credentials_block=args.aws_credentials_block,
            dry_run=args.dry_run,
        )
    )
