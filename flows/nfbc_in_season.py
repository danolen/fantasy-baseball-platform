"""NFBC in-season players + standings Prefect flow (tickets #44, #119).

For each league in ``league_config.csv`` this flow downloads and uploads:

* in-season players export ->
    s3://dn-lakehouse-dev/nfbc/in-season-players/year=/month=/day=/<league>.csv
* league standings export ->
    s3://dn-lakehouse-dev/nfbc/in-season-standings/league/year=/month=/day=/<league>.csv
* overall (contest-wide) standings export, only for leagues that set
  ``nfbc_overall_game_type_id`` in the seed ->
    s3://dn-lakehouse-dev/nfbc/in-season-standings/overall/year=/month=/day=/<league>.csv

Files are named after the team/league (matching in-season-players). Date
partitions use ``America/New_York`` so keys align with the daily 8 AM ET
schedule and manual uploads from ``utils/upload_folder_to_s3.py`` (local date).

Auth uses the ``nfbc_liu`` session cookie from AWS Secrets Manager plus each
league's ``nfbc_team_id`` from the seed (see ``dbt/seeds/league_config.csv``).
League standings are scoped by the ``team_id`` cookie (like players); overall
standings are scoped by the contest's ``nfbc_overall_game_type_id`` (e.g. 890 =
Online Championship, 897 = NFBC 50).

dbt Cloud job trigger is deferred (no production job yet), consistent with the
other vendor flows.

Run locally without AWS or a Prefect API:
    python flows/nfbc_in_season.py --dry-run

Run locally for real (needs AWS creds for Secrets Manager + S3 PutObject):
    python flows/nfbc_in_season.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from prefect import flow, get_run_logger, task

_FLOWS_DIR = Path(__file__).resolve().parent
if str(_FLOWS_DIR) not in sys.path:
    sys.path.insert(0, str(_FLOWS_DIR))

from hello_flow import _parse_s3_uri, _s3_client  # noqa: E402

DEFAULT_S3_BASE = "s3://dn-lakehouse-dev/nfbc/in-season-players"
DEFAULT_STANDINGS_S3_BASE = "s3://dn-lakehouse-dev/nfbc/in-season-standings"
DEFAULT_DOWNLOAD_BASE = "https://nfc.shgn.com/api/react/players_download"
DEFAULT_LEAGUE_STANDINGS_DOWNLOAD = "https://nfc.shgn.com/standings_download.php"
DEFAULT_OVERALL_STANDINGS_DOWNLOAD = "https://nfc.shgn.com/standings_download_overall.php"
# Standings filter tokens (YTD season standings). The exact league token is
# inferred by analogy with the overall page (auth-gated); override via
# build_*_standings_url if NFBC rejects it on the first authenticated run.
DEFAULT_LEAGUE_STANDINGS_TYPE = "league_season_standings"
DEFAULT_OVERALL_STANDINGS_TYPE = "overall_season_standings"
DEFAULT_LEAGUE_CONFIG = "dbt/seeds/league_config.csv"
DEFAULT_SECRET_NAME = "fantasy-baseball-platform"
DEFAULT_SECRET_REGION = "us-east-1"
DEFAULT_NFBC_LIU_KEY = "nfbc_liu"
DEFAULT_SSID = "14"
DEFAULT_TYPEVAL = "2026"
DOWNLOAD_TIMEOUT_SECONDS = 120
# Match manual S3 uploads (local date) and the Prefect schedule timezone.
PARTITION_TZ = ZoneInfo("America/New_York")


class NfbcAuthError(Exception):
    """Raised when NFBC rejects the session cookie (expired login, etc.)."""


class NfbcDownloadError(Exception):
    """Raised when a single league download or upload fails."""


@dataclass(frozen=True)
class LeagueConfig:
    league: str
    nfbc_team_id: int
    # Contest game_type_id for overall (contest-wide) standings; None when the
    # league has no overall standings (only nolen_oc / nolen_50 today).
    nfbc_overall_game_type_id: int | None = None


def build_download_url(
    *,
    ssid: str = DEFAULT_SSID,
    typeval: str = DEFAULT_TYPEVAL,
    sport: str = "baseball",
    stattype: str = "season",
) -> str:
    query = urlencode(
        {
            "sport": sport,
            "stattype": stattype,
            "typeval": typeval,
            "ssid": ssid,
        }
    )
    return f"{DEFAULT_DOWNLOAD_BASE}?{query}"


def build_league_standings_url(
    *,
    ssid: str = DEFAULT_SSID,
    typeval: str = DEFAULT_TYPEVAL,
    sport: str = "baseball",
    standings_type: str = DEFAULT_LEAGUE_STANDINGS_TYPE,
) -> str:
    """League standings CSV download (scoped by the team_id cookie)."""
    query = urlencode(
        {
            "sport": sport,
            "standings_type": standings_type,
            "typeval": typeval,
            "ssid": ssid,
        }
    )
    return f"{DEFAULT_LEAGUE_STANDINGS_DOWNLOAD}?{query}"


def build_overall_standings_url(
    *,
    game_type_id: int,
    ssid: str = DEFAULT_SSID,
    typeval: str = DEFAULT_TYPEVAL,
    sport: str = "baseball",
    standings_type: str = DEFAULT_OVERALL_STANDINGS_TYPE,
) -> str:
    """Overall (contest-wide) standings CSV download, scoped by game_type_id."""
    query = urlencode(
        {
            "sport": sport,
            "game_type_id": game_type_id,
            "standings_type": standings_type,
            "typeval": typeval,
            "ssid": ssid,
        }
    )
    return f"{DEFAULT_OVERALL_STANDINGS_DOWNLOAD}?{query}"


def build_cookie_header(liu: str, team_id: int) -> str:
    return f"liu={liu.strip()}; team_id={team_id}"


def build_csv_s3_key(base_prefix: str, stamp: datetime, league: str) -> str:
    partition = f"year={stamp.year}/month={stamp.month:02d}/day={stamp.day:02d}"
    filename = f"{league}.csv"
    return f"{base_prefix}/{partition}/{filename}" if base_prefix else f"{partition}/{filename}"


def partition_stamp(tz: ZoneInfo = PARTITION_TZ) -> datetime:
    """Return the current wall-clock time in the partition timezone."""
    return datetime.now(tz)

def load_league_config(path: str | Path) -> list[LeagueConfig]:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"League config not found: {config_path}")

    leagues: list[LeagueConfig] = []
    with config_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "nfbc_team_id" not in reader.fieldnames:
            raise ValueError(f"{config_path} must include an nfbc_team_id column")
        for row in reader:
            league = (row.get("league") or "").strip()
            team_id_raw = (row.get("nfbc_team_id") or "").strip()
            if not league or not team_id_raw:
                continue
            overall_raw = (row.get("nfbc_overall_game_type_id") or "").strip()
            overall_game_type_id = int(overall_raw) if overall_raw else None
            leagues.append(
                LeagueConfig(
                    league=league,
                    nfbc_team_id=int(team_id_raw),
                    nfbc_overall_game_type_id=overall_game_type_id,
                )
            )

    if not leagues:
        raise ValueError(f"No leagues found in {config_path}")
    return leagues


def validate_players_csv(body: bytes) -> None:
    """Ensure the NFBC response looks like a league-scoped players CSV."""
    if not body:
        raise NfbcAuthError("NFBC returned an empty response (session may be expired)")

    text = body.decode("utf-8-sig", errors="replace").lstrip()
    lowered = text.lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html"):
        raise NfbcAuthError(
            "NFBC returned HTML instead of CSV (session cookie likely expired)"
        )

    first_line = text.splitlines()[0] if text else ""
    if "owner" not in first_line.lower():
        raise NfbcAuthError(
            "NFBC CSV is missing the Owner column (session cookie likely expired "
            "or team_id cookie not applied)"
        )


def validate_standings_csv(body: bytes) -> None:
    """Ensure the NFBC response looks like a standings CSV (not a login page)."""
    if not body:
        raise NfbcAuthError(
            "NFBC returned an empty standings response (session may be expired)"
        )

    text = body.decode("utf-8-sig", errors="replace").lstrip()
    lowered = text.lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html"):
        raise NfbcAuthError(
            "NFBC returned HTML instead of standings CSV (session cookie likely "
            "expired)"
        )

    first_line = text.splitlines()[0] if text else ""
    if "team" not in first_line.lower():
        raise NfbcAuthError(
            "NFBC standings CSV is missing the Team column (session cookie likely "
            "expired or filter params rejected)"
        )


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


def fetch_nfbc_liu(
    *,
    secret_name: str,
    secret_region: str,
    secret_key: str,
    aws_credentials_block: str | None = None,
) -> str:
    payload = fetch_secret_json(
        secret_name, region=secret_region, aws_credentials_block=aws_credentials_block
    )
    liu = payload.get(secret_key)
    if not liu or not str(liu).strip():
        raise ValueError(
            f"Secret {secret_name} is missing key {secret_key!r} for NFBC auth"
        )
    return str(liu).strip()


def download_players_csv(
    *,
    liu: str,
    team_id: int,
    download_url: str,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    response = requests.get(
        download_url,
        headers={"Cookie": build_cookie_header(liu, team_id)},
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        raise NfbcDownloadError(
            f"NFBC HTTP {response.status_code} for team_id={team_id}"
        )

    body = response.content
    try:
        validate_players_csv(body)
    except NfbcAuthError:
        raise
    except Exception as exc:
        raise NfbcDownloadError(f"Invalid NFBC CSV for team_id={team_id}: {exc}") from exc
    return body


def download_standings_csv(
    *,
    liu: str,
    team_id: int,
    download_url: str,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    """Download a league or overall standings CSV using the session cookie."""
    response = requests.get(
        download_url,
        headers={"Cookie": build_cookie_header(liu, team_id)},
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        raise NfbcDownloadError(
            f"NFBC HTTP {response.status_code} for team_id={team_id}"
        )

    body = response.content
    try:
        validate_standings_csv(body)
    except NfbcAuthError:
        raise
    except Exception as exc:
        raise NfbcDownloadError(
            f"Invalid NFBC standings CSV for team_id={team_id}: {exc}"
        ) from exc
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
def ingest_league(
    league: LeagueConfig,
    *,
    liu: str,
    download_url: str,
    bucket: str,
    base_prefix: str,
    stamp: datetime,
    aws_credentials_block: str | None,
    dry_run: bool,
) -> str:
    logger = get_run_logger()
    key = build_csv_s3_key(base_prefix, stamp, league.league)
    target = f"s3://{bucket}/{key}"

    if dry_run:
        logger.info(
            "DRY RUN — would download team_id=%s and upload %s",
            league.nfbc_team_id,
            target,
        )
        return target

    body = download_players_csv(
        liu=liu,
        team_id=league.nfbc_team_id,
        download_url=download_url,
    )
    uri = put_csv_object(bucket, key, body, aws_credentials_block)
    logger.info("Uploaded %s (%s bytes)", uri, len(body))
    return uri


@task
def ingest_standings(
    league: LeagueConfig,
    *,
    liu: str,
    download_url: str,
    bucket: str,
    base_prefix: str,
    stamp: datetime,
    kind: str,
    aws_credentials_block: str | None,
    dry_run: bool,
) -> str:
    """Download and upload one standings CSV (kind = 'league' or 'overall')."""
    logger = get_run_logger()
    key = build_csv_s3_key(base_prefix, stamp, league.league)
    target = f"s3://{bucket}/{key}"

    if dry_run:
        logger.info(
            "DRY RUN — would download %s standings team_id=%s from %s and upload %s",
            kind,
            league.nfbc_team_id,
            download_url,
            target,
        )
        return target

    body = download_standings_csv(
        liu=liu,
        team_id=league.nfbc_team_id,
        download_url=download_url,
    )
    uri = put_csv_object(bucket, key, body, aws_credentials_block)
    logger.info("Uploaded %s standings %s (%s bytes)", kind, uri, len(body))
    return uri


@flow(name="nfbc-in-season")
def nfbc_in_season(
    s3_base_path: str = DEFAULT_S3_BASE,
    standings_s3_base_path: str = DEFAULT_STANDINGS_S3_BASE,
    league_config_path: str = DEFAULT_LEAGUE_CONFIG,
    secret_name: str = DEFAULT_SECRET_NAME,
    secret_region: str = DEFAULT_SECRET_REGION,
    secret_key: str = DEFAULT_NFBC_LIU_KEY,
    download_url: str | None = None,
    league_standings_download_url: str | None = None,
    ssid: str = DEFAULT_SSID,
    typeval: str = DEFAULT_TYPEVAL,
    include_players: bool = True,
    include_standings: bool = True,
    aws_credentials_block: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Download NFBC in-season player + standings CSVs for all configured leagues."""
    logger = get_run_logger()
    stamp = partition_stamp()
    bucket, base_prefix = _parse_s3_uri(s3_base_path)
    standings_bucket, standings_base_prefix = _parse_s3_uri(standings_s3_base_path)
    league_standings_prefix = f"{standings_base_prefix}/league".lstrip("/")
    overall_standings_prefix = f"{standings_base_prefix}/overall".lstrip("/")
    leagues = load_league_config(league_config_path)
    resolved_download_url = download_url or build_download_url(ssid=ssid, typeval=typeval)
    resolved_league_standings_url = league_standings_download_url or (
        build_league_standings_url(ssid=ssid, typeval=typeval)
    )

    liu = "dry-run-liu"
    if not dry_run:
        liu = fetch_nfbc_liu(
            secret_name=secret_name,
            secret_region=secret_region,
            secret_key=secret_key,
            aws_credentials_block=aws_credentials_block,
        )

    successes: dict[str, str] = {}
    failures: dict[str, str] = {}

    def _run(label: str, fn) -> None:
        """Run one ingest, isolating failures but re-raising NFBC auth errors."""
        try:
            successes[label] = fn()
        except NfbcAuthError:
            logger.exception("NFBC auth failed for %s", label)
            raise
        except Exception as exc:
            message = str(exc)
            failures[label] = message
            logger.error("%s failed: %s", label, message)

    for league in leagues:
        if include_players:
            _run(
                f"{league.league} players",
                lambda league=league: ingest_league(
                    league,
                    liu=liu,
                    download_url=resolved_download_url,
                    bucket=bucket,
                    base_prefix=base_prefix,
                    stamp=stamp,
                    aws_credentials_block=aws_credentials_block,
                    dry_run=dry_run,
                ),
            )

        if include_standings:
            _run(
                f"{league.league} league-standings",
                lambda league=league: ingest_standings(
                    league,
                    liu=liu,
                    download_url=resolved_league_standings_url,
                    bucket=standings_bucket,
                    base_prefix=league_standings_prefix,
                    stamp=stamp,
                    kind="league",
                    aws_credentials_block=aws_credentials_block,
                    dry_run=dry_run,
                ),
            )

            if league.nfbc_overall_game_type_id is not None:
                overall_url = build_overall_standings_url(
                    game_type_id=league.nfbc_overall_game_type_id,
                    ssid=ssid,
                    typeval=typeval,
                )
                _run(
                    f"{league.league} overall-standings",
                    lambda league=league, overall_url=overall_url: ingest_standings(
                        league,
                        liu=liu,
                        download_url=overall_url,
                        bucket=standings_bucket,
                        base_prefix=overall_standings_prefix,
                        stamp=stamp,
                        kind="overall",
                        aws_credentials_block=aws_credentials_block,
                        dry_run=dry_run,
                    ),
                )

    summary = {"successes": successes, "failures": failures}
    logger.info("NFBC in-season ingest complete: %s", summary)

    if failures:
        failed = ", ".join(f"{name} ({reason})" for name, reason in failures.items())
        raise NfbcDownloadError(
            f"{len(failures)} ingest(s) failed after others succeeded: {failed}"
        )

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the NFBC in-season Prefect flow.")
    parser.add_argument(
        "--s3-path",
        default=DEFAULT_S3_BASE,
        help=f"Players base S3 URI (default: {DEFAULT_S3_BASE})",
    )
    parser.add_argument(
        "--standings-s3-path",
        default=DEFAULT_STANDINGS_S3_BASE,
        help=(
            "Standings base S3 URI; league/ and overall/ subdirs are added "
            f"automatically (default: {DEFAULT_STANDINGS_S3_BASE})"
        ),
    )
    parser.add_argument(
        "--league-config",
        default=DEFAULT_LEAGUE_CONFIG,
        help=f"Path to league_config.csv (default: {DEFAULT_LEAGUE_CONFIG})",
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
        "--typeval",
        default=DEFAULT_TYPEVAL,
        help=f"NFBC season year query param (default: {DEFAULT_TYPEVAL})",
    )
    parser.add_argument(
        "--aws-credentials-block",
        default=None,
        help="Name of a Prefect AwsCredentials block (for Prefect Managed compute).",
    )
    parser.add_argument(
        "--skip-players",
        action="store_true",
        help="Skip the in-season players download (standings only).",
    )
    parser.add_argument(
        "--skip-standings",
        action="store_true",
        help="Skip the standings downloads (players only).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned downloads/uploads instead of calling NFBC/AWS.",
    )
    args = parser.parse_args()
    print(
        nfbc_in_season(
            s3_base_path=args.s3_path,
            standings_s3_base_path=args.standings_s3_path,
            league_config_path=args.league_config,
            secret_name=args.secret_name,
            secret_region=args.secret_region,
            typeval=args.typeval,
            include_players=not args.skip_players,
            include_standings=not args.skip_standings,
            aws_credentials_block=args.aws_credentials_block,
            dry_run=args.dry_run,
        )
    )
