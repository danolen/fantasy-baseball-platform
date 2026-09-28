#!/usr/bin/env python3
"""Capture one NFBC draft-results league to a local CSV (#299).

Snake-draft pick log only. Auction-equivalent dollars of the pick slot are
a later dbt join, not this ingest.

``POST /draft_results.data.php`` is login-gated but was not Cloudflare-
challenged from a datacenter client (unlike ``/claims`` and league
``standings.data.php``). One-time pull with session cookies, or parse a
browser-saved HTML fragment that contains ``#tbl_draft_results``.

Default is Nolen OC (1828). Spike a Main Event league by passing ids::

    export NFBC_LIU=...   # value only, not liu=
    export NFBC_JWT=...   # optional
    python scripts/nfbc_draft_results.py
    python scripts/nfbc_draft_results.py --league-id 1055 --format main_event

    python scripts/nfbc_draft_results.py --html ./draft_1828.html
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
DOWNLOAD_TIMEOUT_SECONDS = 60


class DraftParseError(ValueError):
    """The draft-results HTML is missing the pick table or has a new layout."""


class DraftFetchError(RuntimeError):
    """Could not load a logged-in draft-results table."""


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
            "Capture one NFBC snake-draft results table to CSV. "
            "No auction-equivalent dollars (later dbt model). "
            f"Default league {DEFAULT_LEAGUE_ID} (Nolen OC)."
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        html = load_draft_html(args)
        rows = parse_draft_results_html(
            html,
            league_id=args.league_id,
            format=args.format,
            season=args.season,
        )
        output = Path(args.output) if args.output else default_output_path(args.league_id)
        write_draft_csv(output, rows)
        rounds = sorted({row.round for row in rows})
        print(
            f"Wrote {len(rows)} picks across {len(rounds)} rounds "
            f"(picks {rows[0].pick}-{rows[-1].pick}) → {output}"
        )
    except (DraftParseError, DraftFetchError) as exc:
        print(f"Failed to capture draft results: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
