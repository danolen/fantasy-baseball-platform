#!/usr/bin/env python3
"""Capture NFBC claims pages to S3 (#298).

NFBC ``/claims`` is blocked by Cloudflare for ``requests``-style clients
(same as league ``standings.data.php``). This is a one-time, end-of-season
operator script you run on your machine. No Prefect.

Recommended: start a debug Chrome, log in, then attach and upload every
2026 Main Event + Online Championship league (qualifiers excluded)::

    # quit Chrome first, then:
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
      --remote-debugging-port=9222 \\
      --user-data-dir="$HOME/.cache/nfbc-claims-chrome"

    # in that window, log in at nfc.shgn.com, then:
    python scripts/nfbc_claims.py --connect-cdp http://127.0.0.1:9222 --all --s3

One league only (default 1828)::

    python scripts/nfbc_claims.py --connect-cdp http://127.0.0.1:9222 --s3
    python scripts/nfbc_claims.py --html ./claims_1828.html --s3

Local CSVs are named ``claims_{league_id}.csv``. After a successful S3
put, that exact file is deleted. Other files are never touched. Use
``--keep-local`` to skip the delete. S3 keys use today's
``America/New_York`` date partition::

    s3://dn-lakehouse-dev/nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv
    s3://dn-lakehouse-dev/nfbc/claims/main_event/year=2026/month=09/day=27/claims_{league_id}.csv

Already-uploaded unpartitioned objects can be moved with
``scripts/nfbc_claims_repartition.py``.
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
from zoneinfo import ZoneInfo

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
DEFAULT_S3_BASE = "s3://dn-lakehouse-dev/nfbc/claims"
CLAIMS_CSV_NAME_RE = re.compile(r"^claims_(\d+)\.csv$")
DEFAULT_PAUSE_SECONDS = 1.0
PARTITION_TZ = ZoneInfo("America/New_York")


class ClaimsParseError(ValueError):
    """The claims HTML is missing tables or has an unexpected layout."""


class ClaimsFetchError(RuntimeError):
    """The local browser did not reach a logged-in claims page."""


class ClaimsUploadError(RuntimeError):
    """S3 upload or the post-upload local delete failed."""


@dataclass(frozen=True)
class ClaimsLeague:
    league_id: int
    format: str
    label: str


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


def classify_claims_option(text: str) -> str | None:
    """Return ``main_event`` / ``online_championship`` or None (out of scope)."""
    lowered = " ".join(text.lower().split())
    if lowered.startswith("main event"):
        return "main_event"
    if "online championship" in lowered and "qualifier" not in lowered:
        return "online_championship"
    return None


def parse_claims_league_index(html: str) -> list[ClaimsLeague]:
    """Read ME + OC league_ids from the claims ``#league_id`` dropdown."""
    BeautifulSoup = _require_bs4()
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", id="league_id")
    if select is None:
        raise ClaimsParseError("Claims page has no #league_id dropdown")

    seen: set[int] = set()
    leagues: list[ClaimsLeague] = []
    for option in select.find_all("option"):
        raw = (option.get("value") or "").strip()
        if not raw.isdigit():
            continue
        league_id = int(raw)
        if league_id in seen:
            continue
        label = option.get_text(" ", strip=True)
        fmt = classify_claims_option(label)
        if fmt is None:
            continue
        seen.add(league_id)
        leagues.append(ClaimsLeague(league_id=league_id, format=fmt, label=label))
    if not leagues:
        raise ClaimsParseError("No Main Event or Online Championship leagues in dropdown")
    return leagues


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


def default_output_path(league_id: int, output_dir: Path | None = None) -> Path:
    parent = output_dir if output_dir is not None else Path.cwd()
    return parent / f"claims_{league_id}.csv"


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ClaimsUploadError("S3 path must start with s3://")
    parts = [p for p in s3_uri[len("s3://") :].split("/") if p]
    if not parts:
        raise ClaimsUploadError("S3 path must include a bucket")
    return parts[0], "/".join(parts[1:])


def date_partition_path(when: date | None = None) -> str:
    """Hive-style ``year=/month=/day=`` folder used by other NFBC ingest prefixes."""
    day = when or datetime.now(PARTITION_TZ).date()
    return f"year={day.year}/month={day.month:02d}/day={day.day:02d}"


def claims_s3_key(
    base_prefix: str,
    format: str,
    league_id: int,
    *,
    when: date | None = None,
) -> str:
    if format not in FORMAT_CHOICES:
        raise ClaimsUploadError(f"Invalid claims format {format!r}")
    filename = f"claims_{league_id}.csv"
    folder = f"{base_prefix}/{format}" if base_prefix else format
    return f"{folder}/{date_partition_path(when)}/{filename}"


def claims_s3_uri(
    s3_base: str,
    format: str,
    league_id: int,
    *,
    when: date | None = None,
) -> str:
    bucket, prefix = parse_s3_uri(s3_base)
    key = claims_s3_key(prefix, format, league_id, when=when)
    return f"s3://{bucket}/{key}"


def is_unpartitioned_claims_key(key: str, base_prefix: str) -> bool:
    """True for ``{prefix}/{format}/claims_{id}.csv`` with no date partition."""
    expected = f"{base_prefix}/" if base_prefix else ""
    if expected and not key.startswith(expected):
        return False
    rest = key[len(expected) :]
    parts = rest.split("/")
    if len(parts) != 2:
        return False
    fmt, name = parts
    return fmt in FORMAT_CHOICES and CLAIMS_CSV_NAME_RE.fullmatch(name) is not None


def partitioned_claims_dest_key(source_key: str, *, when: date | None = None) -> str:
    """Keep the format folder; insert today's partition before the filename."""
    if "/" not in source_key:
        raise ClaimsUploadError(f"Cannot partition key with no folder: {source_key}")
    parent, name = source_key.rsplit("/", 1)
    return f"{parent}/{date_partition_path(when)}/{name}"


def upload_claims_csv(
    path: Path,
    *,
    s3_base: str,
    format: str,
    league_id: int,
    s3_client=None,
    when: date | None = None,
) -> str:
    """Put one local claims CSV to S3. Does not delete anything."""
    if not path.is_file() or path.is_symlink():
        raise ClaimsUploadError(f"Refusing to upload missing or symlink path: {path}")
    bucket, prefix = parse_s3_uri(s3_base)
    key = claims_s3_key(prefix, format, league_id, when=when)
    body = path.read_bytes()
    if not body:
        raise ClaimsUploadError(f"Refusing to upload empty file: {path}")
    client = s3_client
    if client is None:
        import boto3

        client = boto3.client("s3")
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/csv")
    except Exception as exc:
        raise ClaimsUploadError(f"S3 put failed for s3://{bucket}/{key}: {exc}") from exc
    return f"s3://{bucket}/{key}"


def is_safe_claims_csv_path(path: Path, *, league_id: int) -> bool:
    """True only for a regular file named claims_{league_id}.csv."""
    if path.is_symlink() or path.is_dir():
        return False
    match = CLAIMS_CSV_NAME_RE.fullmatch(path.name)
    if match is None:
        return False
    return int(match.group(1)) == league_id and path.is_file()


def delete_local_claims_csv(path: Path, *, league_id: int) -> None:
    """Delete only the claims CSV we just wrote for this league_id."""
    if not is_safe_claims_csv_path(path, league_id=league_id):
        raise ClaimsUploadError(
            f"Refusing to delete {path}; only a regular file named "
            f"claims_{league_id}.csv written by this run may be removed"
        )
    path.unlink()
    if path.exists():
        raise ClaimsUploadError(f"Delete reported success but {path} still exists")


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


class ClaimsBrowser:
    """One Chrome session that can load many ``/claims?league_id=`` pages."""

    def __init__(
        self,
        *,
        headed: bool = True,
        user_data_dir: Path | None = None,
        timeout_seconds: int = DEFAULT_BROWSER_TIMEOUT_SECONDS,
        cookies: NfbcBrowserCookies | None = None,
        channel: str = DEFAULT_BROWSER_CHANNEL,
        connect_cdp: str | None = None,
    ) -> None:
        self.headed = headed
        self.user_data_dir = user_data_dir or DEFAULT_USER_DATA_DIR
        self.timeout_seconds = timeout_seconds
        self.cookies = cookies
        self.channel = channel
        self.connect_cdp = connect_cdp
        self._playwright = None
        self._context = None
        self._page = None
        self._owns_context = False
        self._Timeout = None

    def __enter__(self) -> "ClaimsBrowser":
        sync_playwright, timeout_cls = _require_playwright()
        self._Timeout = timeout_cls
        self._playwright = sync_playwright().start()
        launch_args = ["--disable-blink-features=AutomationControlled"]
        if sys.platform.startswith("linux"):
            launch_args.extend(["--no-sandbox", "--disable-dev-shm-usage"])

        if self.connect_cdp:
            print(
                f"Attaching to Chrome at {self.connect_cdp}. "
                f"Waiting up to {self.timeout_seconds}s per league …",
                file=sys.stderr,
            )
            browser = self._playwright.chromium.connect_over_cdp(self.connect_cdp)
            self._context = (
                browser.contexts[0] if browser.contexts else browser.new_context()
            )
            self._page = self._context.new_page()
        else:
            if self.headed:
                print(
                    f"Opening {self.channel}. If the Cloudflare box loops, "
                    "use --connect-cdp.",
                    file=sys.stderr,
                )
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            launch_kwargs: dict = {
                "headless": not self.headed,
                "args": launch_args,
                "viewport": {"width": 1400, "height": 900},
                "ignore_default_args": ["--enable-automation"],
            }
            if self.channel == "chrome":
                launch_kwargs["channel"] = "chrome"
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(self.user_data_dir),
                    **launch_kwargs,
                )
            except Exception as exc:
                self.close()
                raise ClaimsFetchError(
                    f"Could not launch {self.channel}. Install Google Chrome "
                    "or pass --channel chromium. If Cloudflare loops, "
                    f"attach instead:\n  {chrome_debug_command()}\n"
                    f"  python scripts/nfbc_claims.py --connect-cdp "
                    f"{DEFAULT_CDP_URL}\n({exc})"
                ) from exc
            self._owns_context = True
            if self.cookies is not None:
                self._context.add_cookies(playwright_cookie_list(self.cookies))
            self._page = (
                self._context.pages[0] if self._context.pages else self._context.new_page()
            )
        return self

    def fetch(self, league_id: int) -> str:
        if self._page is None or self._Timeout is None:
            raise ClaimsFetchError("Browser session is not open")
        url = claims_page_url(league_id)
        timeout_ms = self.timeout_seconds * 1000
        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            self._page.wait_for_selector(CLAIMS_TABLE_SELECTOR, timeout=timeout_ms)
        except self._Timeout as exc:
            html = self._page.content()
            title = self._page.title()
            if _page_looks_challenged(html, title):
                raise ClaimsFetchError(
                    _cloudflare_help(profile=self.user_data_dir)
                ) from exc
            raise ClaimsFetchError(
                "Claims tables did not appear. Log in at nfc.shgn.com, "
                f"or attach to an already-logged-in Chrome with "
                f"--connect-cdp {DEFAULT_CDP_URL}."
            ) from exc
        return self._page.content()

    def close(self) -> None:
        if self._page is not None and self.connect_cdp:
            self._page.close()
        self._page = None
        if self._owns_context and self._context is not None:
            self._context.close()
        self._context = None
        self._owns_context = False
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


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
    with ClaimsBrowser(
        headed=headed,
        user_data_dir=user_data_dir,
        timeout_seconds=timeout_seconds,
        cookies=cookies,
        channel=channel,
        connect_cdp=connect_cdp,
    ) as browser:
        return browser.fetch(league_id)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture NFBC claims to a local CSV and optionally S3. "
            "Default: one league (1828). Use --all for every 2026 ME+OC league."
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
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Capture every Main Event and Online Championship league listed "
            "on the claims dropdown. Qualifiers are skipped. Requires a "
            "browser session, not --html."
        ),
    )
    parser.add_argument(
        "--s3",
        action="store_true",
        help=(
            "Upload each claims CSV to S3, then delete that local CSV. "
            f"Default prefix: {DEFAULT_S3_BASE}/{{format}}/"
            "year=/month=/day=/claims_{id}.csv"
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
        help="After a successful S3 upload, keep the local claims CSV.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help="Sleep between leagues when using --all (default: 1).",
    )
    return parser.parse_args(argv)


def _browser_kwargs(args: argparse.Namespace) -> dict:
    user_data_dir = (
        Path(args.user_data_dir) if args.user_data_dir else DEFAULT_USER_DATA_DIR
    )
    return {
        "headed": args.headed,
        "user_data_dir": user_data_dir,
        "timeout_seconds": args.timeout_seconds,
        "cookies": None if args.connect_cdp else cookies_from_env(),
        "channel": args.channel,
        "connect_cdp": args.connect_cdp,
    }


def load_claims_html(args: argparse.Namespace) -> str:
    if args.html:
        html_path = Path(args.html)
        if not html_path.is_file():
            raise ClaimsFetchError(f"HTML file not found: {html_path}")
        return html_path.read_text(encoding="utf-8", errors="replace")
    return fetch_claims_html_with_browser(args.league_id, **_browser_kwargs(args))


def process_league(
    html: str,
    *,
    league: ClaimsLeague,
    season: int,
    output: Path,
    s3: bool,
    s3_base: str,
    keep_local: bool,
    s3_client=None,
    when: date | None = None,
) -> str | None:
    """Parse one league, write CSV, optionally upload to S3 and delete local."""
    rows = parse_claims_html(
        html,
        league_id=league.league_id,
        format=league.format,
        season=season,
    )
    write_claims_csv(output, rows)
    weeks = sorted({row.faab_date for row in rows})
    print(
        f"Wrote {len(rows)} claims across {len(weeks)} FAAB weeks "
        f"({weeks[0].isoformat()} to {weeks[-1].isoformat()}) → {output}"
    )
    if not s3:
        return None
    uri = upload_claims_csv(
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
    delete_local_claims_csv(output, league_id=league.league_id)
    print(f"Deleted local {output.name}")
    return uri


def _single_league(args: argparse.Namespace) -> ClaimsLeague:
    return ClaimsLeague(
        league_id=args.league_id,
        format=args.format,
        label=f"league {args.league_id}",
    )


def run_all_leagues(args: argparse.Namespace, *, s3_client=None) -> int:
    if args.html:
        raise ClaimsFetchError("--all needs a live browser session, not --html")
    failures: list[str] = []
    uploaded = 0
    with ClaimsBrowser(**_browser_kwargs(args)) as browser:
        seed_html = browser.fetch(args.league_id)
        leagues = parse_claims_league_index(seed_html)
        print(
            f"Found {len(leagues)} ME/OC leagues "
            f"({sum(1 for item in leagues if item.format == 'main_event')} ME, "
            f"{sum(1 for item in leagues if item.format == 'online_championship')} OC)",
            file=sys.stderr,
        )
        cached = {args.league_id: seed_html}
        for index, league in enumerate(leagues, start=1):
            print(
                f"[{index}/{len(leagues)}] {league.format} {league.league_id} "
                f"{league.label}",
                file=sys.stderr,
            )
            try:
                html = cached.pop(league.league_id, None) or browser.fetch(
                    league.league_id
                )
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
            except (ClaimsParseError, ClaimsFetchError, ClaimsUploadError) as exc:
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
        html = load_claims_html(args)
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
    except (ClaimsParseError, ClaimsFetchError, ClaimsUploadError) as exc:
        print(f"Failed to capture claims: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
