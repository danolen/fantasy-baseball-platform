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

Auth uses the full NFBC session cookie stored in AWS Secrets Manager under the
``nfbc_cookie`` key (falls back to ``nfbc_liu`` for players only). Players use
``api/react/players_download`` scoped by each league's ``nfbc_team_id``.

Standings have no NFBC CSV export, so the flow POSTs the legacy
``standings.data.php`` / ``standings_overall.data.php`` endpoints (the same
requests the standings pages make) and parses the returned HTML table into CSV:

* league standings: POST ``standings.data.php`` with the league's
  ``nfbc_league_id`` (table ``#standings_league``).
* overall standings: POST ``standings_overall.data.php`` with the contest's
  ``nfbc_overall_game_type_id`` (table ``#standings_overall_1``), only for
  leagues that set it (890 = Online Championship, 897 = NFBC 50).

These legacy endpoints need the full browser cookie (not just ``liu``), which is
why ``nfbc_cookie`` is required for standings.

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
import io
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
# Standings are not exported as CSV by NFBC; the flow POSTs the same legacy
# endpoints the standings pages use and parses the returned HTML tables.
LEAGUE_STANDINGS_DATA_URL = "https://nfc.shgn.com/standings.data.php"
OVERALL_STANDINGS_DATA_URL = "https://nfc.shgn.com/standings_overall.data.php"
LEAGUE_STANDINGS_REFERER = "https://nfc.shgn.com/standings"
OVERALL_STANDINGS_REFERER = "https://nfc.shgn.com/standings_overall"
LEAGUE_STANDINGS_TABLE_ID = "standings_league"
OVERALL_STANDINGS_TABLE_ID = "standings_overall_1"
# YTD season standings views (matching the maintainer's browser capture).
DEFAULT_LEAGUE_STANDINGS_TYPE = "league_season_standings"
DEFAULT_OVERALL_STANDINGS_TYPE = "overall_season_standings"
DEFAULT_LEAGUE_STANDINGS_VIEW = "classic"
DEFAULT_OVERALL_STANDINGS_VIEW_TYPE = "overview"
DEFAULT_SPID = "14"
DEFAULT_LEAGUE_CONFIG = "dbt/seeds/league_config.csv"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_SECRET_NAME = "fantasy-baseball-platform"
DEFAULT_SECRET_REGION = "us-east-1"
DEFAULT_NFBC_LIU_KEY = "nfbc_liu"
DEFAULT_NFBC_COOKIE_KEY = "nfbc_cookie"
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
    # League id used to POST league standings (from the /standings dropdown).
    nfbc_league_id: int | None = None
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


def build_league_standings_form(
    league_id: int,
    *,
    spid: str = DEFAULT_SPID,
    standings_type: str = DEFAULT_LEAGUE_STANDINGS_TYPE,
    view: str = DEFAULT_LEAGUE_STANDINGS_VIEW,
) -> dict[str, str]:
    """POST body for the league standings endpoint."""
    return {
        "league_id": str(league_id),
        "spid": spid,
        "standings_type": standings_type,
        "view": view,
    }


def build_overall_standings_form(
    game_type_id: int,
    *,
    spid: str = DEFAULT_SPID,
    sport: str = "baseball",
    standings_type: str = DEFAULT_OVERALL_STANDINGS_TYPE,
    view_type: str = DEFAULT_OVERALL_STANDINGS_VIEW_TYPE,
) -> dict[str, str]:
    """POST body for the overall (contest-wide) standings endpoint."""
    return {
        "sport": sport,
        "game_type_id": str(game_type_id),
        "spid": spid,
        "standings_type": standings_type,
        "view_type": view_type,
    }


def build_cookie_header(liu: str, team_id: int) -> str:
    return f"liu={liu.strip()}; team_id={team_id}"


def parse_cookie_value(cookie: str, name: str) -> str | None:
    """Extract a single cookie value (e.g. ``liu``) from a full cookie header."""
    for part in cookie.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


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
            league_id_raw = (row.get("nfbc_league_id") or "").strip()
            league_id = int(league_id_raw) if league_id_raw else None
            leagues.append(
                LeagueConfig(
                    league=league,
                    nfbc_team_id=int(team_id_raw),
                    nfbc_league_id=league_id,
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


def standings_html_to_csv(html: str, table_id: str) -> bytes:
    """Parse an NFBC standings HTML fragment into CSV bytes.

    The standings endpoints return an HTML table (the same one the page renders).
    Rows with a single cell (the table title) are skipped; the remaining header +
    data rows are written as CSV (csv quoting handles thousands separators).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=table_id)
    if table is None:
        raise NfbcAuthError(
            f"NFBC standings table #{table_id} not found "
            "(session cookie likely expired or filter params rejected)"
        )

    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) <= 1:
            continue
        rows.append([cell.get_text(strip=True) for cell in cells])

    if len(rows) < 2:
        raise NfbcDownloadError(
            f"NFBC standings table #{table_id} had no data rows"
        )

    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8")


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


def fetch_nfbc_auth(
    *,
    secret_name: str,
    secret_region: str,
    cookie_key: str = DEFAULT_NFBC_COOKIE_KEY,
    liu_key: str = DEFAULT_NFBC_LIU_KEY,
    aws_credentials_block: str | None = None,
) -> tuple[str, str | None]:
    """Return ``(liu, full_cookie)`` from Secrets Manager.

    Prefers the full ``nfbc_cookie`` (required for standings; ``liu`` is parsed
    out of it for players). Falls back to the legacy ``nfbc_liu`` key, in which
    case the returned cookie is ``None`` and standings cannot run.
    """
    payload = fetch_secret_json(
        secret_name, region=secret_region, aws_credentials_block=aws_credentials_block
    )

    cookie_raw = payload.get(cookie_key)
    cookie = str(cookie_raw).strip() if cookie_raw and str(cookie_raw).strip() else None

    if cookie:
        liu = parse_cookie_value(cookie, "liu")
    else:
        liu_raw = payload.get(liu_key)
        liu = str(liu_raw).strip() if liu_raw and str(liu_raw).strip() else None

    if not liu:
        raise ValueError(
            f"Secret {secret_name} is missing NFBC auth: set {cookie_key!r} to the "
            f"full session cookie (preferred) or {liu_key!r} to the liu value"
        )
    return liu, cookie


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
    cookie: str,
    post_url: str,
    form: dict[str, str],
    table_id: str,
    referer: str,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    """POST a legacy standings endpoint and parse the HTML table into CSV.

    NFBC has no standings CSV export, so the flow POSTs ``standings.data.php`` /
    ``standings_overall.data.php`` (the same requests the standings pages make)
    with the full session cookie and parses the returned HTML table. These legacy
    endpoints require the full browser cookie, not just ``liu``.
    """
    response = requests.post(
        post_url,
        data=form,
        headers={
            "Cookie": cookie,
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "text/html, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://nfc.shgn.com",
            "Referer": referer,
        },
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        snippet = response.text[:200].replace("\n", " ").strip()
        raise NfbcDownloadError(
            f"NFBC HTTP {response.status_code} for {post_url} form={form}: {snippet}"
        )

    try:
        return standings_html_to_csv(response.text, table_id)
    except (NfbcAuthError, NfbcDownloadError):
        raise
    except Exception as exc:
        raise NfbcDownloadError(
            f"Failed to parse NFBC standings ({post_url} form={form}): {exc}"
        ) from exc


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
    cookie: str | None,
    post_url: str,
    form: dict[str, str],
    table_id: str,
    referer: str,
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
            "DRY RUN — would POST %s %s standings (%s) and upload %s",
            post_url,
            kind,
            form,
            target,
        )
        return target

    if not cookie:
        raise NfbcDownloadError(
            f"{kind} standings need the full session cookie; set the "
            f"{DEFAULT_NFBC_COOKIE_KEY!r} secret key"
        )

    body = download_standings_csv(
        cookie=cookie,
        post_url=post_url,
        form=form,
        table_id=table_id,
        referer=referer,
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
    cookie_key: str = DEFAULT_NFBC_COOKIE_KEY,
    liu_key: str = DEFAULT_NFBC_LIU_KEY,
    download_url: str | None = None,
    ssid: str = DEFAULT_SSID,
    typeval: str = DEFAULT_TYPEVAL,
    spid: str = DEFAULT_SPID,
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

    liu = "dry-run-liu"
    cookie: str | None = "dry-run-cookie"
    if not dry_run:
        liu, cookie = fetch_nfbc_auth(
            secret_name=secret_name,
            secret_region=secret_region,
            cookie_key=cookie_key,
            liu_key=liu_key,
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
            if league.nfbc_league_id is not None:
                league_form = build_league_standings_form(
                    league.nfbc_league_id, spid=spid
                )
                _run(
                    f"{league.league} league-standings",
                    lambda league=league, league_form=league_form: ingest_standings(
                        league,
                        cookie=cookie,
                        post_url=LEAGUE_STANDINGS_DATA_URL,
                        form=league_form,
                        table_id=LEAGUE_STANDINGS_TABLE_ID,
                        referer=LEAGUE_STANDINGS_REFERER,
                        bucket=standings_bucket,
                        base_prefix=league_standings_prefix,
                        stamp=stamp,
                        kind="league",
                        aws_credentials_block=aws_credentials_block,
                        dry_run=dry_run,
                    ),
                )
            else:
                logger.warning(
                    "Skipping league standings for %s (no nfbc_league_id in seed)",
                    league.league,
                )

            if league.nfbc_overall_game_type_id is not None:
                overall_form = build_overall_standings_form(
                    league.nfbc_overall_game_type_id, spid=spid
                )
                _run(
                    f"{league.league} overall-standings",
                    lambda league=league, overall_form=overall_form: ingest_standings(
                        league,
                        cookie=cookie,
                        post_url=OVERALL_STANDINGS_DATA_URL,
                        form=overall_form,
                        table_id=OVERALL_STANDINGS_TABLE_ID,
                        referer=OVERALL_STANDINGS_REFERER,
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
