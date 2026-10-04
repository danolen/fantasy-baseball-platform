"""Local NFBC team-stats HTML parser spike (#300)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nfbc_teamstats import (  # noqa: E402
    TeamstatsFetchError,
    TeamstatsParseError,
    cookies_from_env,
    fetch_teamstats_html,
    format_grain,
    load_teamstats_html,
    main,
    parse_args,
    parse_teamstats_html,
    summarize_grain,
    write_teamstats_csv,
)

FIXTURE = ROOT / "tests" / "fixtures" / "nfbc_teamstats_sample.html"


def _parse(html: str | None = None, **kwargs):
    text = html if html is not None else FIXTURE.read_text(encoding="utf-8")
    defaults = {
        "league_id": 1828,
        "team_id": 770000,
        "week": 26,
        "season": 2026,
    }
    defaults.update(kwargs)
    return parse_teamstats_html(text, **defaults)


def test_fixture_records_bench_and_active_rows():
    rows = _parse()
    grain = summarize_grain(rows)
    assert grain.row_count == 4
    assert grain.bench_present is True
    assert grain.bench_row_count == 1
    assert grain.sections == ("Hitting", "Bench", "Pitching")
    by_name = {row.player: row for row in rows}
    assert by_name["Ohtani, Shohei"].is_bench is False
    assert by_name["Ohtani, Shohei"].player_id == 9810
    assert by_name["Ohtani, Shohei"].position == "UT"
    assert "AB=24" in by_name["Ohtani, Shohei"].stats
    assert by_name["Pederson, Joc"].is_bench is True
    assert by_name["Pederson, Joc"].section == "Bench"
    assert "NO — #301" not in format_grain(grain)


def test_write_csv_and_cli(tmp_path: Path):
    rows = _parse()
    out = tmp_path / "teamstats_1828_770000_26.csv"
    write_teamstats_csv(out, rows)
    with out.open(encoding="utf-8", newline="") as handle:
        table = list(csv.DictReader(handle))
    assert [row["player"] for row in table] == [
        "Ohtani, Shohei",
        "Judge, Aaron",
        "Pederson, Joc",
        "Soroka, Michael",
    ]
    bench = next(row for row in table if row["player"] == "Pederson, Joc")
    assert bench["is_bench"] == "true"
    assert bench["week"] == "26"

    cli_out = tmp_path / "from_cli.csv"
    rc = main(
        [
            "--html",
            str(FIXTURE),
            "--output",
            str(cli_out),
        ]
    )
    assert rc == 0
    assert "Pederson, Joc" in cli_out.read_text(encoding="utf-8")


def test_login_wall_is_a_fetch_error():
    with pytest.raises(TeamstatsFetchError, match="login wall"):
        _parse("Must be logged in to view this page.")


def test_missing_table_raises():
    with pytest.raises(TeamstatsParseError, match="No player tables"):
        _parse("<html><body>no table</body></html>")


def test_cli_defaults_to_nolen_oc_week_26():
    args = parse_args([])
    assert args.league_id == 1828
    assert args.team_id == 770000
    assert args.week == 26


def test_cookies_from_env_are_optional_and_normalized():
    assert cookies_from_env({}) is None
    auth = cookies_from_env({"NFBC_LIU": "liu=abc", "NFBC_JWT": "jwt=def"})
    assert auth == ("abc", "def")


def test_load_html_reads_saved_file():
    args = parse_args(["--html", str(FIXTURE)])
    html = load_teamstats_html(args)
    assert "Ohtani, Shohei" in html


def test_missing_html_and_cookies_is_a_fetch_error(monkeypatch):
    monkeypatch.delenv("NFBC_LIU", raising=False)
    args = parse_args([])
    with pytest.raises(TeamstatsFetchError, match="--html"):
        load_teamstats_html(args)


def test_cloudflare_403_is_a_fetch_error(monkeypatch):
    def raise_403(*args, **kwargs):
        raise HTTPError(
            url="https://nfc.shgn.com/teamstats/1828/770000/26",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("nfbc_teamstats.urlopen", raise_403)
    with pytest.raises(TeamstatsFetchError, match="Cloudflare"):
        fetch_teamstats_html(1828, 770000, 26, liu="abc")


def test_cloudflare_body_is_a_fetch_error(monkeypatch):
    response = MagicMock()
    response.read.return_value = b"<html>Just a moment</html>"
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    monkeypatch.setattr("nfbc_teamstats.urlopen", lambda *args, **kwargs: response)
    with pytest.raises(TeamstatsFetchError, match="Cloudflare"):
        fetch_teamstats_html(1828, 770000, 26, liu="abc")
