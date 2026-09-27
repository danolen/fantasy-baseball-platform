#!/usr/bin/env python3
"""Capture one NFBC claims league to a local CSV (#298).

NFBC ``/claims`` is blocked by Cloudflare for ``requests``-style clients
(same as league ``standings.data.php``). This is a one-time, end-of-season
operator script you run on your machine. No Prefect, no S3 in this iteration.

This chunk covers one Online Championship league (default ``1828``, Nolen OC).
The all-league loop and S3 upload come later.

Default: attach to a Chrome window you already started, or launch installed
Google Chrome (not Playwright's bundled Chromium). Playwright Chromium is
what Cloudflare's "click this box if you are a human" loop detects.

Recommended when the checkbox keeps coming back — start Chrome yourself,
log in there, then attach:

    # quit Chrome first, then:
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
      --remote-debugging-port=9222 \\
      --user-data-dir="$HOME/.cache/nfbc-claims-chrome"

    # in that window, log in at nfc.shgn.com, then:
    python scripts/nfbc_claims.py --connect-cdp http://127.0.0.1:9222

    python scripts/nfbc_claims.py --html ./claims_1828.html

Writes ``claims_1828.csv`` in the current directory.

Optional login shortcuts (values only, never printed):
    export NFBC_LIU='…'    # nfc.shgn.com ``liu`` cookie
    export NFBC_JWT='…'    # optional ``jwt`` cookie

These cookies do not skip Cloudflare. Prefer ``--connect-cdp``.

Later S3 layout (not implemented here)::

    s3://dn-lakehouse-dev/nfbc/claims/online_championship/claims_1828.csv
    s3://dn-lakehouse-dev/nfbc/claims/main_event/claims_{league_id}.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PLAYER_HREF_RE = re.compile(r"/player/baseball/(\d+)/", re.I)
WEEK_TITLE_RE = re.compile(
    r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+FAAB Winning Bids$",
    re.I,
)
CLAIMS_TABLE_CLASS = "claims"
CLAIMS_TABLE_SELECTOR = "table.claims"
EXPECTED_HEADERS = ("Team", "Add", "Drop", "Bid", "Runner-Up")
CSV_COLUMNS = (
    "league_id",
    "format",
    "season",
    "faab_date",
    "team",
    "add_player",
    "add_player_id",
    "drop_player",
    "drop_player_id",
    "bid",
    "runner_up",
)
DEFAULT_LEAGUE_ID = 1828
DEFAULT_FORMAT = "online_championship"
DEFAULT_SEASON = 2026
FORMAT_CHOICES = ("online_championship", "main_event")
CLAIMS_PAGE_URL = "https://nfc.shgn.com/claims?league_id={league_id}"
COOKIE_DOMAIN = "nfc.shgn.com"
DEFAULT_BROWSER_TIMEOUT_SECONDS = 300
DEFAULT_USER_DATA_DIR = Path.home() / ".cache" / "nfbc-claims-browser"
DEFAULT_BROWSER_CHANNEL = "chrome"
BROWSER_CHANNEL_CHOICES = ("chrome", "chromium")
DEFAULT_CDP_URL = "http://127.0.0.1:9222"


class ClaimsParseError(ValueError):
    """The claims HTML is missing tables or has an unexpected layout."""


class ClaimsFetchError(RuntimeError):
    """The local browser did not reach a logged-in claims page."""


@dataclass(frozen=True)
class ClaimsRow:
    league_id: int
    format: str
    season: int
    faab_date: date
    team: str
    add_player: str
    add_player_id: int | None
    drop_player: str
    drop_player_id: int | None
    bid: int
    runner_up: int | None


@dataclass(frozen=True)
class NfbcBrowserCookies:
    liu: str
    jwt: str | None = None


def _require_bs4():
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise SystemExit(
            "beautifulsoup4 is required. Install with: "
            "pip install beautifulsoup4"
        ) from exc
    return BeautifulSoup


def _require_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "playwright is required for --browser. Install with: "
            "pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightTimeout


def claims_page_url(league_id: int) -> str:
    return CLAIMS_PAGE_URL.format(league_id=league_id)


def chrome_debug_command(*, user_data_dir: Path | None = None) -> str:
    """Shell command to start a debug Chrome the script can attach to."""
    profile = user_data_dir or (Path.home() / ".cache" / "nfbc-claims-chrome")
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
) -> NfbcBrowserCookies | None:
    """Read ``NFBC_LIU`` / ``NFBC_JWT`` if set. Does not print values."""
    env = os.environ if environ is None else environ
    liu = (env.get("NFBC_LIU") or "").strip()
    if not liu:
        return None
    jwt = (env.get("NFBC_JWT") or "").strip() or None
    if liu.lower().startswith("liu="):
        liu = liu[4:].strip()
    if jwt and jwt.lower().startswith("jwt="):
        jwt = jwt[4:].strip()
    return NfbcBrowserCookies(liu=liu, jwt=jwt)


def playwright_cookie_list(auth: NfbcBrowserCookies) -> list[dict[str, str]]:
    cookies = [
        {
            "name": "liu",
            "value": auth.liu,
            "domain": COOKIE_DOMAIN,
            "path": "/",
        }
    ]
    if auth.jwt:
        cookies.append(
            {
                "name": "jwt",
                "value": auth.jwt,
                "domain": COOKIE_DOMAIN,
                "path": "/",
            }
        )
    return cookies


def parse_week_title(title: str, *, season: int) -> date:
    """Turn ``9/20 FAAB Winning Bids`` into a date in ``season``."""
    match = WEEK_TITLE_RE.match(title.strip())
    if match is None:
        raise ClaimsParseError(f"Unrecognized claims week title: {title!r}")
    return date(season, int(match.group("month")), int(match.group("day")))


def parse_player_cell(cell) -> tuple[str, int | None]:
    """Return (player name, nfbc player id) from an Add/Drop cell."""
    link = cell.find("a")
    if link is not None:
        name = link.get_text(" ", strip=True)
        href = link.get("href") or ""
        match = PLAYER_HREF_RE.search(href)
        player_id = int(match.group(1)) if match else None
        return name, player_id
    return cell.get_text(" ", strip=True), None


def parse_optional_int(raw: str) -> int | None:
    text = raw.strip().replace(",", "").replace("$", "")
    if text in ("", "-"):
        return None
    return int(text)


def selected_league_id(soup) -> int | None:
    select = soup.find("select", id="league_id")
    if select is None:
        return None
    selected = select.find("option", selected=True)
    if selected is None:
        for option in select.find_all("option"):
            if option.has_attr("selected"):
                selected = option
                break
    if selected is None:
        return None
    raw = (selected.get("value") or "").strip()
    return int(raw) if raw.isdigit() else None


def parse_claims_html(
    html: str,
    *,
    league_id: int,
    format: str,
    season: int,
) -> list[ClaimsRow]:
    """Parse a full NFBC claims document into typed rows."""
    if format not in FORMAT_CHOICES:
        raise ClaimsParseError(
            f"format must be one of {FORMAT_CHOICES}, got {format!r}"
        )

    BeautifulSoup = _require_bs4()
    soup = BeautifulSoup(html, "html.parser")

    page_league_id = selected_league_id(soup)
    if page_league_id is not None and page_league_id != league_id:
        raise ClaimsParseError(
            f"HTML is for league_id={page_league_id}, but --league-id={league_id}"
        )

    tables = soup.find_all(
        "table", class_=lambda classes: classes and CLAIMS_TABLE_CLASS in classes
    )
    if not tables:
        raise ClaimsParseError(
            "No tables with class 'claims' found. Log in in the browser "
            "window, or pass a full /claims page with --html."
        )

    rows: list[ClaimsRow] = []
    for table in tables:
        trs = table.find_all("tr")
        if len(trs) < 3:
            continue
        title = trs[0].get_text(" ", strip=True)
        faab_date = parse_week_title(title, season=season)
        headers = [cell.get_text(" ", strip=True) for cell in trs[1].find_all(["th", "td"])]
        if tuple(headers) != EXPECTED_HEADERS:
            raise ClaimsParseError(
                f"Unexpected claims headers {headers} under {title!r}"
            )
        for tr in trs[2:]:
            cells = tr.find_all("td")
            if len(cells) < 5:
                continue
            add_name, add_id = parse_player_cell(cells[1])
            drop_name, drop_id = parse_player_cell(cells[2])
            bid = parse_optional_int(cells[3].get_text(" ", strip=True))
            if bid is None:
                raise ClaimsParseError(
                    f"Winning bid missing in {title!r} for team "
                    f"{cells[0].get_text(' ', strip=True)!r}"
                )
            rows.append(
                ClaimsRow(
                    league_id=league_id,
                    format=format,
                    season=season,
                    faab_date=faab_date,
                    team=cells[0].get_text(" ", strip=True),
                    add_player=add_name,
                    add_player_id=add_id,
                    drop_player=drop_name,
                    drop_player_id=drop_id,
                    bid=bid,
                    runner_up=parse_optional_int(cells[4].get_text(" ", strip=True)),
                )
            )

    if not rows:
        raise ClaimsParseError("Claims tables were present but had no data rows")
    return rows


def rows_to_dicts(rows: list[ClaimsRow]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        out.append(
            {
                "league_id": str(row.league_id),
                "format": row.format,
                "season": str(row.season),
                "faab_date": row.faab_date.isoformat(),
                "team": row.team,
                "add_player": row.add_player,
                "add_player_id": "" if row.add_player_id is None else str(row.add_player_id),
                "drop_player": row.drop_player,
                "drop_player_id": "" if row.drop_player_id is None else str(row.drop_player_id),
                "bid": str(row.bid),
                "runner_up": "" if row.runner_up is None else str(row.runner_up),
            }
        )
    return out


def write_claims_csv(path: Path, rows: list[ClaimsRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows_to_dicts(rows))


def default_output_path(league_id: int) -> Path:
    return Path(f"claims_{league_id}.csv")


def _page_looks_challenged(html: str, title: str | None) -> bool:
    blob = f"{title or ''} {html[:4000]}".lower()
    return (
        "just a moment" in blob
        or "attention required" in blob
        or "cf-mitigated" in blob
        or "__cf_chl" in html
    )


def _cloudflare_help(*, profile: Path) -> str:
    return (
        "Cloudflare is treating this as an automated browser (the checkbox "
        "loops). Playwright's bundled Chromium is the usual cause. Quit "
        "Chrome, start a debug Chrome, log in there, then attach:\n"
        f"  {chrome_debug_command()}\n"
        f"  python scripts/nfbc_claims.py --connect-cdp {DEFAULT_CDP_URL}\n"
        f"Launched-browser profile was {profile}."
    )


def fetch_claims_html_with_browser(
    league_id: int,
    *,
    headed: bool = True,
    user_data_dir: Path | None = None,
    timeout_seconds: int = DEFAULT_BROWSER_TIMEOUT_SECONDS,
    cookies: NfbcBrowserCookies | None = None,
    channel: str = DEFAULT_BROWSER_CHANNEL,
    connect_cdp: str | None = None,
) -> str:
    """Open ``/claims?league_id=…`` and return the page HTML."""
    sync_playwright, PlaywrightTimeout = _require_playwright()
    url = claims_page_url(league_id)
    profile = user_data_dir or DEFAULT_USER_DATA_DIR
    timeout_ms = timeout_seconds * 1000

    if connect_cdp:
        print(
            f"Attaching to Chrome at {connect_cdp} and opening {url}. "
            f"Waiting up to {timeout_seconds}s for table.claims …",
            file=sys.stderr,
        )
    elif headed:
        print(
            f"Opening {url} in {channel}. If the Cloudflare box loops, "
            f"use --connect-cdp. Waiting up to {timeout_seconds}s …",
            file=sys.stderr,
        )

    launch_args = ["--disable-blink-features=AutomationControlled"]
    if sys.platform.startswith("linux"):
        launch_args.extend(["--no-sandbox", "--disable-dev-shm-usage"])

    with sync_playwright() as playwright:
        owns_context = False
        page = None
        try:
            if connect_cdp:
                browser = playwright.chromium.connect_over_cdp(connect_cdp)
                context = (
                    browser.contexts[0] if browser.contexts else browser.new_context()
                )
                page = context.new_page()
            else:
                profile.mkdir(parents=True, exist_ok=True)
                launch_kwargs: dict = {
                    "headless": not headed,
                    "args": launch_args,
                    "viewport": {"width": 1400, "height": 900},
                    "ignore_default_args": ["--enable-automation"],
                }
                if channel == "chrome":
                    launch_kwargs["channel"] = "chrome"
                try:
                    context = playwright.chromium.launch_persistent_context(
                        str(profile),
                        **launch_kwargs,
                    )
                except Exception as exc:
                    raise ClaimsFetchError(
                        f"Could not launch {channel}. Install Google Chrome "
                        "or pass --channel chromium. If Cloudflare loops, "
                        f"attach instead:\n  {chrome_debug_command()}\n"
                        f"  python scripts/nfbc_claims.py --connect-cdp "
                        f"{DEFAULT_CDP_URL}\n({exc})"
                    ) from exc
                owns_context = True
                if cookies is not None:
                    context.add_cookies(playwright_cookie_list(cookies))
                page = context.pages[0] if context.pages else context.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_selector(CLAIMS_TABLE_SELECTOR, timeout=timeout_ms)
            except PlaywrightTimeout as exc:
                html = page.content()
                title = page.title()
                if _page_looks_challenged(html, title):
                    raise ClaimsFetchError(_cloudflare_help(profile=profile)) from exc
                raise ClaimsFetchError(
                    "Claims tables did not appear. Log in at nfc.shgn.com, "
                    f"or attach to an already-logged-in Chrome with "
                    f"--connect-cdp {DEFAULT_CDP_URL}."
                ) from exc
            return page.content()
        finally:
            if page is not None and connect_cdp:
                page.close()
            if owns_context:
                context.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture one NFBC claims league to a local CSV. "
            "Default: open installed Chrome for Nolen OC (1828)."
        )
    )
    parser.add_argument(
        "--html",
        default=None,
        help="Parse a browser-saved claims page instead of opening a browser.",
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
        help=f"Season year for FAAB dates (default: {DEFAULT_SEASON}).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: ./claims_<league_id>.csv).",
    )
    parser.add_argument(
        "--headed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show the browser so you can log in (default: headed).",
    )
    parser.add_argument(
        "--channel",
        choices=BROWSER_CHANNEL_CHOICES,
        default=DEFAULT_BROWSER_CHANNEL,
        help="Browser to launch when not using --connect-cdp (default: chrome).",
    )
    parser.add_argument(
        "--connect-cdp",
        default=None,
        metavar="URL",
        help=(
            "Attach to a Chrome you started with --remote-debugging-port "
            f"(example: {DEFAULT_CDP_URL}). Use this if the Cloudflare "
            "checkbox loops."
        ),
    )
    parser.add_argument(
        "--user-data-dir",
        default=None,
        help=(
            "Persistent launched-browser profile (default: "
            f"{DEFAULT_USER_DATA_DIR}). Ignored with --connect-cdp."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_BROWSER_TIMEOUT_SECONDS,
        help="How long to wait for table.claims after opening the page.",
    )
    return parser.parse_args(argv)


def load_claims_html(args: argparse.Namespace) -> str:
    if args.html:
        html_path = Path(args.html)
        if not html_path.is_file():
            raise ClaimsFetchError(f"HTML file not found: {html_path}")
        return html_path.read_text(encoding="utf-8", errors="replace")

    user_data_dir = (
        Path(args.user_data_dir) if args.user_data_dir else DEFAULT_USER_DATA_DIR
    )
    return fetch_claims_html_with_browser(
        args.league_id,
        headed=args.headed,
        user_data_dir=user_data_dir,
        timeout_seconds=args.timeout_seconds,
        cookies=None if args.connect_cdp else cookies_from_env(),
        channel=args.channel,
        connect_cdp=args.connect_cdp,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        html = load_claims_html(args)
        rows = parse_claims_html(
            html,
            league_id=args.league_id,
            format=args.format,
            season=args.season,
        )
    except (ClaimsParseError, ClaimsFetchError) as exc:
        print(f"Failed to capture claims: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output) if args.output else default_output_path(args.league_id)
    write_claims_csv(output, rows)
    weeks = sorted({row.faab_date for row in rows})
    print(
        f"Wrote {len(rows)} claims across {len(weeks)} FAAB weeks "
        f"({weeks[0].isoformat()} to {weeks[-1].isoformat()}) → {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
