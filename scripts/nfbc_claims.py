#!/usr/bin/env python3
"""Parse a browser-saved NFBC claims page into a local CSV (#298).

NFBC ``/claims`` is blocked by Cloudflare for scripted clients (same as
league ``standings.data.php``). This is a one-time, end-of-season operator
script: save the claims page from a logged-in browser, then parse it here.
No Prefect, no cookies, no S3 in this iteration.

This chunk covers one Online Championship league (default ``1828``, Nolen OC).
S3 upload and the full ME/OC league loop come later.

Save the page
-------------
1. Log in at https://nfc.shgn.com/claims?league_id=1828
2. File → Save Page As… (complete HTML), or DevTools → the document
   response, as ``claims_1828.html``.
3. Do not commit the HTML — the saved page can include session tokens.

Run
---
    python scripts/nfbc_claims.py --html ./claims_1828.html

Writes ``claims_1828.csv`` in the current directory. Override with
``--output`` / ``--league-id`` / ``--format``.

Later S3 layout (not implemented here)::

    s3://dn-lakehouse-dev/nfbc/claims/online_championship/claims_1828.csv
    s3://dn-lakehouse-dev/nfbc/claims/main_event/claims_{league_id}.csv
"""

from __future__ import annotations

import argparse
import csv
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


class ClaimsParseError(ValueError):
    """The saved HTML is missing claims tables or has an unexpected layout."""


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


def _require_bs4():
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise SystemExit(
            "beautifulsoup4 is required. Install with: "
            "pip install beautifulsoup4"
        ) from exc
    return BeautifulSoup


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
            "No tables with class 'claims' found. Save the full /claims page "
            "from a logged-in browser."
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse a browser-saved NFBC claims HTML page into a local CSV. "
            "Default league is Nolen OC (1828)."
        )
    )
    parser.add_argument(
        "--html",
        required=True,
        help="Path to a browser-saved claims page (e.g. claims_1828.html).",
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    html_path = Path(args.html)
    if not html_path.is_file():
        print(f"HTML file not found: {html_path}", file=sys.stderr)
        return 1

    html = html_path.read_text(encoding="utf-8", errors="replace")
    try:
        rows = parse_claims_html(
            html,
            league_id=args.league_id,
            format=args.format,
            season=args.season,
        )
    except ClaimsParseError as exc:
        print(f"Failed to parse {html_path}: {exc}", file=sys.stderr)
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
