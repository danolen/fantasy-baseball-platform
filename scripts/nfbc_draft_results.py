#!/usr/bin/env python3
"""Capture NFBC draft results to S3 (#299).

Snake-draft pick log only. Auction-equivalent dollars of the pick slot are
a later dbt join, not this ingest.

``POST /draft_results.data.php`` is login-gated but was not Cloudflare-
challenged from a datacenter client (unlike ``/claims``). Cookie POSTs are
the path a Prefect flow can reuse in draft season. How to discover
**next** season's league_ids is a follow-up; this season's ME/OC list is
the ``#league_id`` dropdown on the draft-results page (60 ME + 240 OC,
same as overall standings).

Default is Nolen OC (1828)::

    export NFBC_LIU=...   # value only, not liu=
    export NFBC_JWT=...   # optional
    python scripts/nfbc_draft_results.py
    python scripts/nfbc_draft_results.py --league-id 1055 --format main_event
    python scripts/nfbc_draft_results.py --all --s3
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

PLAYER_HREF_RE = re.compile(r"/player/baseball/(\d+)/", re.I)
TEAM_ATTR_RE = re.compile(r"^team_(\d+)$", re.I)
TABLE_ID = "tbl_draft_results"
EXPECTED_HEADERS = ("Player", "Round", "Pick", "Pos", "Team", "Duration")
CSV_COLUMNS = (
    "league_id",
    "format",
    "season",
    "round",
    "pick",
    "player",
    "player_id",
    "position",
    "team",
    "team_id",
    "duration",
)
DEFAULT_LEAGUE_ID = 1828
DEFAULT_FORMAT = "online_championship"
DEFAULT_SEASON = 2026
FORMAT_CHOICES = ("online_championship", "main_event")
DATA_URL = "https://nfc.shgn.com/draft_results.data.php"
INDEX_URL = "https://nfc.shgn.com/draftresults/baseball"
DEFAULT_S3_BASE = "s3://dn-lakehouse-dev/nfbc/draft-results"
DRAFT_CSV_NAME_RE = re.compile(r"^draft_(\d+)\.csv$")
DOWNLOAD_TIMEOUT_SECONDS = 60
DEFAULT_PAUSE_SECONDS = 1.0
PARTITION_TZ = ZoneInfo("America/New_York")


class DraftParseError(ValueError):
    """The draft-results HTML is missing the pick table or has a new layout."""


class DraftFetchError(RuntimeError):
    """Could not load a logged-in draft-results table."""


class DraftUploadError(RuntimeError):
    """S3 upload or the post-upload local delete failed."""


@dataclass(frozen=True)
class DraftLeague:
    league_id: int
    format: str
    label: str


@dataclass(frozen=True)
class DraftPick:
    league_id: int
    format: str
    season: int
    round: int
    pick: int
    player: str
    player_id: int | None
    position: str
    team: str
    team_id: int | None
    duration: str


def _require_bs4():
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise SystemExit(
            "beautifulsoup4 is required. Install with: pip install beautifulsoup4"
        ) from exc
    return BeautifulSoup


def cookies_from_env(
    environ: dict[str, str] | None = None,
) -> tuple[str, str | None] | None:
    env = os.environ if environ is None else environ
    liu = (env.get("NFBC_LIU") or "").strip()
    if not liu:
        return None
    jwt = (env.get("NFBC_JWT") or "").strip() or None
    if liu.lower().startswith("liu="):
        liu = liu[4:].strip()
    if jwt and jwt.lower().startswith("jwt="):
        jwt = jwt[4:].strip()
    return liu, jwt


def cookie_header(liu: str, jwt: str | None) -> str:
    header = f"liu={liu}"
    if jwt:
        header += f"; jwt={jwt}"
    return header


def parse_player_cell(cell) -> tuple[str, int | None]:
    link = cell.find("a")
    if link is not None:
        name = link.get_text(" ", strip=True)
        href = link.get("href") or ""
        match = PLAYER_HREF_RE.search(href)
        player_id = int(match.group(1)) if match else None
        return name, player_id
    return cell.get_text(" ", strip=True), None


def parse_optional_attr_id(raw: str | None, pattern: re.Pattern[str]) -> int | None:
    if not raw:
        return None
    match = pattern.fullmatch(raw.strip())
    return int(match.group(1)) if match else None


def parse_draft_results_html(
    html: str,
    *,
    league_id: int,
    format: str,
    season: int,
) -> list[DraftPick]:
    if format not in FORMAT_CHOICES:
        raise DraftParseError(f"format must be one of {FORMAT_CHOICES}, got {format!r}")
    if "Must be logged in to view this page." in html:
        raise DraftFetchError("NFBC returned the login wall for draft results")

    BeautifulSoup = _require_bs4()
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=TABLE_ID)
    if table is None:
        raise DraftParseError(
            "No #tbl_draft_results table. Pass a logged-in draft-results "
            "response (POST /draft_results.data.php) with --html, or set "
            "NFBC_LIU."
        )

    rows: list[DraftPick] = []
    header_seen = False
    for tr in table.find_all("tr"):
        header_cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("th")]
        if tuple(header_cells) == EXPECTED_HEADERS:
            header_seen = True
            continue
        if not header_seen:
            continue
        cells = tr.find_all("td")
        if len(cells) < 6:
            continue
        player, player_id = parse_player_cell(cells[0])
        round_no = int(cells[1].get_text(" ", strip=True))
        pick_no = int(cells[2].get_text(" ", strip=True))
        position = cells[3].get_text(" ", strip=True)
        team = cells[4].get_text(" ", strip=True)
        duration = cells[5].get_text(" ", strip=True)
        rows.append(
            DraftPick(
                league_id=league_id,
                format=format,
                season=season,
                round=round_no,
                pick=pick_no,
                player=player,
                player_id=player_id,
                position=position,
                team=team,
                team_id=parse_optional_attr_id(tr.get("data-team"), TEAM_ATTR_RE),
                duration=duration,
            )
        )

    if not header_seen:
        raise DraftParseError(f"Unexpected draft-results headers, wanted {EXPECTED_HEADERS}")
    if not rows:
        raise DraftParseError("Draft-results table was present but had no pick rows")
    return rows


def rows_to_dicts(rows: list[DraftPick]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        out.append(
            {
                "league_id": str(row.league_id),
                "format": row.format,
                "season": str(row.season),
                "round": str(row.round),
                "pick": str(row.pick),
                "player": row.player,
                "player_id": "" if row.player_id is None else str(row.player_id),
                "position": row.position,
                "team": row.team,
                "team_id": "" if row.team_id is None else str(row.team_id),
                "duration": row.duration,
            }
        )
    return out


def write_draft_csv(path: Path, rows: list[DraftPick]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows_to_dicts(rows))


def default_output_path(league_id: int, output_dir: Path | None = None) -> Path:
    parent = output_dir if output_dir is not None else Path.cwd()
    return parent / f"draft_{league_id}.csv"


def classify_draft_option(text: str) -> str | None:
    """Return ``main_event`` / ``online_championship`` or None (out of scope)."""
    lowered = " ".join(text.lower().split())
    if "qualifier" in lowered:
        return None
    if lowered.startswith("main event"):
        return "main_event"
    if "online championship" in lowered:
        return "online_championship"
    return None


def parse_draft_league_index(html: str) -> list[DraftLeague]:
    """Read ME + OC league_ids from the draft-results ``#league_id`` dropdown."""
    BeautifulSoup = _require_bs4()
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", id="league_id")
    if select is None:
        raise DraftParseError("Draft-results page has no #league_id dropdown")

    seen: set[int] = set()
    leagues: list[DraftLeague] = []
    for option in select.find_all("option"):
        raw = (option.get("value") or "").strip()
        if not raw.isdigit():
            continue
        league_id = int(raw)
        if league_id in seen:
            continue
        label = option.get_text(" ", strip=True)
        fmt = classify_draft_option(label)
        if fmt is None:
            continue
        seen.add(league_id)
        leagues.append(DraftLeague(league_id=league_id, format=fmt, label=label))
    if not leagues:
        raise DraftParseError("No Main Event or Online Championship leagues in dropdown")
    return leagues


def fetch_draft_league_index_html(
    *,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> str:
    """GET the draft-results shell page (dropdown is public; no login)."""
    request = Request(
        INDEX_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise DraftFetchError(f"GET {INDEX_URL} failed: {exc}") from exc


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise DraftUploadError("S3 path must start with s3://")
    parts = [p for p in s3_uri[len("s3://") :].split("/") if p]
    if not parts:
        raise DraftUploadError("S3 path must include a bucket")
    return parts[0], "/".join(parts[1:])


def date_partition_path(when: date | None = None) -> str:
    day = when or datetime.now(PARTITION_TZ).date()
    return f"year={day.year}/month={day.month:02d}/day={day.day:02d}"


def draft_s3_key(
    base_prefix: str,
    format: str,
    league_id: int,
    *,
    when: date | None = None,
) -> str:
    if format not in FORMAT_CHOICES:
        raise DraftUploadError(f"Invalid draft format {format!r}")
    filename = f"draft_{league_id}.csv"
    folder = f"{base_prefix}/{format}" if base_prefix else format
    return f"{folder}/{date_partition_path(when)}/{filename}"


def draft_s3_uri(
    s3_base: str,
    format: str,
    league_id: int,
    *,
    when: date | None = None,
) -> str:
    bucket, prefix = parse_s3_uri(s3_base)
    key = draft_s3_key(prefix, format, league_id, when=when)
    return f"s3://{bucket}/{key}"


def upload_draft_csv(
    path: Path,
    *,
    s3_base: str,
    format: str,
    league_id: int,
    s3_client=None,
    when: date | None = None,
) -> str:
    """Put one local draft CSV to S3. Does not delete anything."""
    if not path.is_file() or path.is_symlink():
        raise DraftUploadError(f"Refusing to upload missing or symlink path: {path}")
    bucket, prefix = parse_s3_uri(s3_base)
    key = draft_s3_key(prefix, format, league_id, when=when)
    body = path.read_bytes()
    if not body:
        raise DraftUploadError(f"Refusing to upload empty file: {path}")
    client = s3_client
    if client is None:
        import boto3

        client = boto3.client("s3")
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/csv")
    except Exception as exc:
        raise DraftUploadError(f"S3 put failed for s3://{bucket}/{key}: {exc}") from exc
    return f"s3://{bucket}/{key}"


def is_safe_draft_csv_path(path: Path, *, league_id: int) -> bool:
    if path.is_symlink() or path.is_dir():
        return False
    match = DRAFT_CSV_NAME_RE.fullmatch(path.name)
    if match is None:
        return False
    return int(match.group(1)) == league_id and path.is_file()


def delete_local_draft_csv(path: Path, *, league_id: int) -> None:
    if not is_safe_draft_csv_path(path, league_id=league_id):
        raise DraftUploadError(
            f"Refusing to delete {path}; only a regular file named "
            f"draft_{league_id}.csv written by this run may be removed"
        )
    path.unlink()
    if path.exists():
        raise DraftUploadError(f"Delete reported success but {path} still exists")


def fetch_draft_results_html(
    league_id: int,
    *,
    liu: str,
    jwt: str | None = None,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> str:
    body = urlencode(
        {"type": "filter", "league_id": str(league_id), "sport": "baseball"}
    ).encode("utf-8")
    request = Request(
        DATA_URL,
        data=body,
        method="POST",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://nfc.shgn.com/draftresults/baseball",
            "Cookie": cookie_header(liu, jwt),
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise DraftFetchError(f"POST {DATA_URL} failed: {exc}") from exc
    if "Must be logged in to view this page." in html:
        raise DraftFetchError(
            "NFBC session was rejected. Refresh NFBC_LIU / NFBC_JWT and retry."
        )
    if "just a moment" in html.lower() or "__cf_chl" in html:
        raise DraftFetchError(
            "Cloudflare challenged draft_results.data.php. Use the same "
            "headed Chrome / --connect-cdp path as scripts/nfbc_claims.py, "
            "or save the ajax HTML with --html."
        )
    return html


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture NFBC snake-draft results to CSV and optionally S3. "
            "No auction-equivalent dollars (later dbt model). "
            f"Default league {DEFAULT_LEAGUE_ID} (Nolen OC). Use --all for "
            "every 2026 ME+OC league on the draft-results dropdown."
        )
    )
    parser.add_argument(
        "--html",
        default=None,
        help="Parse a saved draft_results.data.php HTML body instead of POSTing.",
    )
    parser.add_argument(
        "--league-id",
        type=int,
        default=DEFAULT_LEAGUE_ID,
        help=f"NFBC league_id (default: {DEFAULT_LEAGUE_ID}, Nolen OC).",
    )
    parser.add_argument(
        "--format",
        choices=FORMAT_CHOICES,
        default=DEFAULT_FORMAT,
        help=f"Contest format for the CSV (default: {DEFAULT_FORMAT}).",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=DEFAULT_SEASON,
        help=f"Season year (default: {DEFAULT_SEASON}).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: ./draft_<league_id>.csv).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Capture every Main Event and Online Championship league listed "
            "on the draft-results dropdown. Qualifiers are skipped. "
            "Requires NFBC_LIU, not --html. Next-season league discovery "
            "is a follow-up."
        ),
    )
    parser.add_argument(
        "--s3",
        action="store_true",
        help=(
            "Upload each draft CSV to S3, then delete that local CSV. "
            f"Default prefix: {DEFAULT_S3_BASE}/{{format}}/"
            "year=/month=/day=/draft_{id}.csv"
        ),
    )
    parser.add_argument(
        "--s3-base",
        default=DEFAULT_S3_BASE,
        help=f"S3 prefix for --s3 (default: {DEFAULT_S3_BASE}).",
    )
    parser.add_argument(
        "--keep-local",
        action="store_true",
        help="After a successful S3 upload, keep the local draft CSV.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help="Sleep between leagues when using --all (default: 1).",
    )
    return parser.parse_args(argv)


def load_draft_html(args: argparse.Namespace) -> str:
    if args.html:
        html_path = Path(args.html)
        if not html_path.is_file():
            raise DraftFetchError(f"HTML file not found: {html_path}")
        return html_path.read_text(encoding="utf-8", errors="replace")
    auth = cookies_from_env()
    if auth is None:
        raise DraftFetchError(
            "Set NFBC_LIU (and optional NFBC_JWT), or pass a saved page with --html."
        )
    liu, jwt = auth
    return fetch_draft_results_html(args.league_id, liu=liu, jwt=jwt)


def process_league(
    html: str,
    *,
    league: DraftLeague,
    season: int,
    output: Path,
    s3: bool,
    s3_base: str,
    keep_local: bool,
    s3_client=None,
    when: date | None = None,
) -> str | None:
    """Parse one league, write CSV, optionally upload to S3 and delete local."""
    rows = parse_draft_results_html(
        html,
        league_id=league.league_id,
        format=league.format,
        season=season,
    )
    write_draft_csv(output, rows)
    rounds = sorted({row.round for row in rows})
    print(
        f"Wrote {len(rows)} picks across {len(rounds)} rounds "
        f"(picks {rows[0].pick}-{rows[-1].pick}) → {output}"
    )
    if not s3:
        return None
    uri = upload_draft_csv(
        output,
        s3_base=s3_base,
        format=league.format,
        league_id=league.league_id,
        s3_client=s3_client,
        when=when,
    )
    print(f"Uploaded {uri}")
    if keep_local:
        return uri
    delete_local_draft_csv(output, league_id=league.league_id)
    print(f"Deleted local {output.name}")
    return uri


def _single_league(args: argparse.Namespace) -> DraftLeague:
    return DraftLeague(
        league_id=args.league_id,
        format=args.format,
        label=f"league {args.league_id}",
    )


def run_all_leagues(
    args: argparse.Namespace,
    *,
    s3_client=None,
    index_html: str | None = None,
    fetch_html=None,
) -> int:
    if args.html:
        raise DraftFetchError("--all needs cookie POSTs, not --html")
    auth = cookies_from_env()
    if auth is None and fetch_html is None:
        raise DraftFetchError("Set NFBC_LIU (and optional NFBC_JWT) for --all.")
    liu, jwt = auth if auth is not None else ("", None)
    getter = fetch_html or (
        lambda league_id: fetch_draft_results_html(league_id, liu=liu, jwt=jwt)
    )
    failures: list[str] = []
    uploaded = 0
    leagues = parse_draft_league_index(
        index_html if index_html is not None else fetch_draft_league_index_html()
    )
    print(
        f"Found {len(leagues)} ME/OC leagues "
        f"({sum(1 for item in leagues if item.format == 'main_event')} ME, "
        f"{sum(1 for item in leagues if item.format == 'online_championship')} OC)",
        file=sys.stderr,
    )
    for index, league in enumerate(leagues, start=1):
        print(
            f"[{index}/{len(leagues)}] {league.format} {league.league_id} "
            f"{league.label}",
            file=sys.stderr,
        )
        try:
            html = getter(league.league_id)
            output = default_output_path(league.league_id)
            process_league(
                html,
                league=league,
                season=args.season,
                output=output,
                s3=args.s3,
                s3_base=args.s3_base,
                keep_local=args.keep_local,
                s3_client=s3_client,
            )
            if args.s3:
                uploaded += 1
        except (DraftParseError, DraftFetchError, DraftUploadError) as exc:
            failures.append(f"{league.league_id}: {exc}")
            print(f"Failed league {league.league_id}: {exc}", file=sys.stderr)
        if index < len(leagues) and args.pause_seconds > 0:
            time.sleep(args.pause_seconds)
    if failures:
        print(
            f"Finished with {len(failures)} failure(s); uploaded {uploaded}.",
            file=sys.stderr,
        )
        for item in failures:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"Finished {len(leagues)} leagues; uploaded {uploaded}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.all:
            return run_all_leagues(args)
        html = load_draft_html(args)
        output = (
            Path(args.output) if args.output else default_output_path(args.league_id)
        )
        process_league(
            html,
            league=_single_league(args),
            season=args.season,
            output=output,
            s3=args.s3,
            s3_base=args.s3_base,
            keep_local=args.keep_local,
        )
    except (DraftParseError, DraftFetchError, DraftUploadError) as exc:
        print(f"Failed to capture draft results: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
