#!/usr/bin/env python3
"""Spike one NFBC weekly team-stats page (#300).

Default is Nolen OC week 26::

    https://nfc.shgn.com/teamstats/1828/770000/26

This VM gets HTTP 403 ``cf-mitigated: challenge`` on that URL (same class as
``/claims`` and league ``standings.data.php``). Cookie GETs do not skip it.
Save the logged-in page, or attach to the same debug Chrome used for claims.

    python scripts/nfbc_teamstats.py --html ./teamstats_1828_770000_26.html

    # quit Chrome first, then:
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
      --remote-debugging-port=9222 \\
      --user-data-dir="$HOME/.cache/nfbc-claims-chrome"
    # log in at nfc.shgn.com in that window, then:
    python scripts/nfbc_teamstats.py --connect-cdp http://127.0.0.1:9222

Writes ``teamstats_1828_770000_26.csv`` and prints whether bench rows were
on the page. No S3. No all-managers loop. Other weeks / teams come after
this week looks right.

#301 missed-value needs bench stats. If this page has none, stop before
designing a workaround.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

PLAYER_HREF_RE = re.compile(r"/player/baseball/(\d+)/", re.I)
BENCH_TOKEN_RE = re.compile(r"\b(bench|reserve|rsv|\bbn\b)\b", re.I)
CSV_COLUMNS = (
    "league_id",
    "team_id",
    "week",
    "season",
    "section",
    "is_bench",
    "player",
    "player_id",
    "position",
    "stats",
)
DEFAULT_LEAGUE_ID = 1828
DEFAULT_TEAM_ID = 770000
DEFAULT_WEEK = 26
DEFAULT_SEASON = 2026
TEAMSTATS_URL = "https://nfc.shgn.com/teamstats/{league_id}/{team_id}/{week}"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_BROWSER_TIMEOUT_SECONDS = 300
DOWNLOAD_TIMEOUT_SECONDS = 60


class TeamstatsParseError(ValueError):
    """The team-stats HTML is missing player tables or has a new layout."""


class TeamstatsFetchError(RuntimeError):
    """Could not load a logged-in team-stats page."""


@dataclass(frozen=True)
class TeamstatsRow:
    league_id: int
    team_id: int
    week: int
    season: int
    section: str
    is_bench: bool
    player: str
    player_id: int | None
    position: str
    stats: str


@dataclass(frozen=True)
class TeamstatsGrain:
    table_count: int
    row_count: int
    bench_row_count: int
    sections: tuple[str, ...]
    bench_present: bool


def _require_bs4():
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise SystemExit(
            "beautifulsoup4 is required. Install with: pip install beautifulsoup4"
        ) from exc
    return BeautifulSoup


def _require_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "playwright is required for --connect-cdp. Install with: "
            "pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightTimeout


def teamstats_page_url(league_id: int, team_id: int, week: int) -> str:
    return TEAMSTATS_URL.format(league_id=league_id, team_id=team_id, week=week)


def chrome_debug_command() -> str:
    profile = Path.home() / ".cache" / "nfbc-claims-chrome"
    if sys.platform == "darwin":
        binary = '"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"'
    elif sys.platform.startswith("win"):
        binary = '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"'
    else:
        binary = "google-chrome"
    return (
        f"{binary} --remote-debugging-port=9222 "
        f'--user-data-dir="{profile}"'
    )


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


def default_output_path(league_id: int, team_id: int, week: int) -> Path:
    return Path.cwd() / f"teamstats_{league_id}_{team_id}_{week}.csv"


def parse_player_cell(cell) -> tuple[str, int | None]:
    link = cell.find("a")
    if link is not None:
        name = link.get_text(" ", strip=True)
        href = link.get("href") or ""
        match = PLAYER_HREF_RE.search(href)
        player_id = int(match.group(1)) if match else None
        return name, player_id
    return cell.get_text(" ", strip=True), None


def looks_like_player_table(headers: list[str]) -> bool:
    lowered = {item.lower() for item in headers}
    return "player" in lowered


def classify_section(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned or "unknown"


def is_bench_label(text: str) -> bool:
    return BENCH_TOKEN_RE.search(text) is not None


def heading_before_table(table) -> str:
    caption = table.find("caption")
    if caption is not None:
        label = caption.get_text(" ", strip=True)
        if label:
            return label
    for sibling in table.previous_siblings:
        if getattr(sibling, "name", None) is None:
            continue
        if sibling.name in {"h1", "h2", "h3", "h4", "h5", "legend"}:
            return sibling.get_text(" ", strip=True)
        if sibling.name in {"p", "div", "span"}:
            label = sibling.get_text(" ", strip=True)
            if label and len(label) <= 40:
                return label
    return ""


def _header_cells(tr) -> list[str]:
    cells = tr.find_all(["th", "td"])
    return [cell.get_text(" ", strip=True) for cell in cells]


def parse_teamstats_html(
    html: str,
    *,
    league_id: int,
    team_id: int,
    week: int,
    season: int,
) -> list[TeamstatsRow]:
    if "Must be logged in to view this page." in html:
        raise TeamstatsFetchError("NFBC returned the login wall for team stats")

    BeautifulSoup = _require_bs4()
    soup = BeautifulSoup(html, "html.parser")
    rows: list[TeamstatsRow] = []

    for table in soup.find_all("table"):
        body_started = False
        headers: list[str] = []
        for tr in table.find_all("tr"):
            cells = _header_cells(tr)
            if not cells:
                continue
            if not body_started and looks_like_player_table(cells):
                headers = cells
                body_started = True
                continue
            if not body_started:
                continue
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            player, player_id = parse_player_cell(tds[0])
            if not player:
                continue
            values = [cell.get_text(" ", strip=True) for cell in tds]
            by_header = {
                headers[index]: values[index]
                for index in range(min(len(headers), len(values)))
                if headers[index].lower() != "player"
            }
            position = ""
            for key, value in by_header.items():
                if key.lower() in {"pos", "position"}:
                    position = value
                    break
            section = classify_section(heading_before_table(table))
            status = ""
            for key, value in by_header.items():
                if key.lower() in {"status", "lineup", "active"}:
                    status = value
                    break
            bench = is_bench_label(section) or is_bench_label(status)
            extras = [
                f"{key}={value}"
                for key, value in by_header.items()
                if key.lower() not in {"pos", "position"}
            ]
            rows.append(
                TeamstatsRow(
                    league_id=league_id,
                    team_id=team_id,
                    week=week,
                    season=season,
                    section=section,
                    is_bench=bench,
                    player=player,
                    player_id=player_id,
                    position=position,
                    stats="; ".join(extras),
                )
            )

    if not rows:
        raise TeamstatsParseError(
            "No player tables. Pass a logged-in team-stats page with --html, "
            "or attach Chrome with --connect-cdp."
        )
    return rows


def summarize_grain(rows: list[TeamstatsRow]) -> TeamstatsGrain:
    sections = tuple(dict.fromkeys(row.section for row in rows))
    bench_rows = sum(1 for row in rows if row.is_bench)
    return TeamstatsGrain(
        table_count=len(sections),
        row_count=len(rows),
        bench_row_count=bench_rows,
        sections=sections,
        bench_present=bench_rows > 0,
    )


def format_grain(grain: TeamstatsGrain) -> str:
    bench = "yes" if grain.bench_present else "NO — #301 missed-value cannot use this page"
    return (
        f"players={grain.row_count} bench_rows={grain.bench_row_count} "
        f"bench_present={bench} sections={list(grain.sections)}"
    )


def rows_to_dicts(rows: list[TeamstatsRow]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        out.append(
            {
                "league_id": str(row.league_id),
                "team_id": str(row.team_id),
                "week": str(row.week),
                "season": str(row.season),
                "section": row.section,
                "is_bench": "true" if row.is_bench else "false",
                "player": row.player,
                "player_id": "" if row.player_id is None else str(row.player_id),
                "position": row.position,
                "stats": row.stats,
            }
        )
    return out


def write_teamstats_csv(path: Path, rows: list[TeamstatsRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows_to_dicts(rows))


def _page_looks_challenged(html: str) -> bool:
    blob = html[:4000].lower()
    return (
        "just a moment" in blob
        or "attention required" in blob
        or "__cf_chl" in html
        or "cf-mitigated" in blob
    )


def _cloudflare_help() -> str:
    return (
        "Cloudflare challenged /teamstats (same class as /claims). "
        "Save the logged-in page with --html, or attach to debug Chrome:\n"
        f"  {chrome_debug_command()}\n"
        f"  python scripts/nfbc_teamstats.py --connect-cdp {DEFAULT_CDP_URL}"
    )


def fetch_teamstats_html(
    league_id: int,
    team_id: int,
    week: int,
    *,
    liu: str,
    jwt: str | None = None,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> str:
    url = teamstats_page_url(league_id, team_id, week)
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Cookie": cookie_header(liu, jwt),
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        status = getattr(exc, "code", None)
        if status == 403:
            raise TeamstatsFetchError(_cloudflare_help()) from exc
        raise TeamstatsFetchError(f"GET {url} failed: {exc}") from exc
    if "Must be logged in to view this page." in html:
        raise TeamstatsFetchError(
            "NFBC session was rejected. Refresh NFBC_LIU / NFBC_JWT, "
            "or use --html / --connect-cdp."
        )
    if _page_looks_challenged(html):
        raise TeamstatsFetchError(_cloudflare_help())
    return html


def fetch_teamstats_html_with_browser(
    league_id: int,
    team_id: int,
    week: int,
    *,
    connect_cdp: str,
    timeout_seconds: int = DEFAULT_BROWSER_TIMEOUT_SECONDS,
) -> str:
    sync_playwright, timeout_cls = _require_playwright()
    url = teamstats_page_url(league_id, team_id, week)
    playwright = sync_playwright().start()
    page = None
    try:
        browser = playwright.chromium.connect_over_cdp(connect_cdp)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        timeout_ms = timeout_seconds * 1000
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_selector(
                "table a[href*='/player/baseball/']", timeout=timeout_ms
            )
        except timeout_cls as exc:
            html = page.content()
            if _page_looks_challenged(html):
                raise TeamstatsFetchError(_cloudflare_help()) from exc
            raise TeamstatsFetchError(
                "Team-stats player table did not appear. Log in at "
                "nfc.shgn.com, or save the page and pass --html."
            ) from exc
        return page.content()
    finally:
        if page is not None:
            page.close()
        playwright.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Spike one NFBC weekly team-stats page to CSV. Default: Nolen OC "
            f"league {DEFAULT_LEAGUE_ID} team {DEFAULT_TEAM_ID} week "
            f"{DEFAULT_WEEK}. No S3. No all-managers loop."
        )
    )
    parser.add_argument(
        "--html",
        default=None,
        help="Parse a saved /teamstats HTML page instead of fetching.",
    )
    parser.add_argument(
        "--league-id",
        type=int,
        default=DEFAULT_LEAGUE_ID,
        help=f"NFBC league_id (default: {DEFAULT_LEAGUE_ID}, Nolen OC).",
    )
    parser.add_argument(
        "--team-id",
        type=int,
        default=DEFAULT_TEAM_ID,
        help=f"NFBC team_id (default: {DEFAULT_TEAM_ID}, Nolen OC).",
    )
    parser.add_argument(
        "--week",
        type=int,
        default=DEFAULT_WEEK,
        help=f"Scoring week (default: {DEFAULT_WEEK}).",
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
        help="Output CSV path (default: ./teamstats_<league>_<team>_<week>.csv).",
    )
    parser.add_argument(
        "--connect-cdp",
        default=None,
        metavar="URL",
        help=(
            "Attach to a Chrome started with --remote-debugging-port "
            f"(example: {DEFAULT_CDP_URL}). Same profile as claims is fine."
        ),
    )
    parser.add_argument(
        "--save-html",
        default=None,
        help="When fetching live, also write the page HTML to this path.",
    )
    return parser.parse_args(argv)


def load_teamstats_html(args: argparse.Namespace) -> str:
    if args.html:
        html_path = Path(args.html)
        if not html_path.is_file():
            raise TeamstatsFetchError(f"HTML file not found: {html_path}")
        return html_path.read_text(encoding="utf-8", errors="replace")
    if args.connect_cdp:
        return fetch_teamstats_html_with_browser(
            args.league_id,
            args.team_id,
            args.week,
            connect_cdp=args.connect_cdp,
        )
    auth = cookies_from_env()
    if auth is None:
        raise TeamstatsFetchError(
            "Set --html, or --connect-cdp, or NFBC_LIU. Cookie GETs are "
            "Cloudflare-challenged from datacenter IPs."
        )
    liu, jwt = auth
    return fetch_teamstats_html(
        args.league_id, args.team_id, args.week, liu=liu, jwt=jwt
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        html = load_teamstats_html(args)
        if args.save_html:
            Path(args.save_html).write_text(html, encoding="utf-8")
        rows = parse_teamstats_html(
            html,
            league_id=args.league_id,
            team_id=args.team_id,
            week=args.week,
            season=args.season,
        )
        output = (
            Path(args.output)
            if args.output
            else default_output_path(args.league_id, args.team_id, args.week)
        )
        write_teamstats_csv(output, rows)
        grain = summarize_grain(rows)
        print(f"Wrote {len(rows)} rows → {output}")
        print(format_grain(grain))
    except (TeamstatsParseError, TeamstatsFetchError) as exc:
        print(f"Failed to capture team stats: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
