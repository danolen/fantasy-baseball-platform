"""Local NFBC claims HTML parser (#298)."""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nfbc_claims import (  # noqa: E402
    ClaimsFetchError,
    ClaimsParseError,
    claims_page_url,
    cookies_from_env,
    load_claims_html,
    main,
    parse_args,
    parse_claims_html,
    parse_week_title,
    playwright_cookie_list,
    write_claims_csv,
    _page_looks_challenged,
)

FIXTURE = ROOT / "tests" / "fixtures" / "nfbc_claims_sample.html"


def _parse(html: str | None = None, **kwargs):
    text = html if html is not None else FIXTURE.read_text(encoding="utf-8")
    defaults = {
        "league_id": 1828,
        "format": "online_championship",
        "season": 2026,
    }
    defaults.update(kwargs)
    return parse_claims_html(text, **defaults)


def test_week_title_uses_season_year():
    assert parse_week_title("9/20 FAAB Winning Bids", season=2026) == date(2026, 9, 20)
    assert parse_week_title("3/22 FAAB Winning Bids", season=2026) == date(2026, 3, 22)


def test_fixture_parses_three_winning_bids():
    rows = _parse()
    assert len(rows) == 3
    assert {row.faab_date for row in rows} == {date(2026, 9, 20), date(2026, 3, 22)}

    first = rows[0]
    assert first.league_id == 1828
    assert first.format == "online_championship"
    assert first.team == "Nolen Squad"
    assert first.add_player == "Michael Soroka"
    assert first.add_player_id == 9791
    assert first.drop_player == "Joc Pederson"
    assert first.drop_player_id == 8688
    assert first.bid == 4
    assert first.runner_up == 2


def test_dash_runner_up_is_blank():
    rows = _parse()
    dash = next(row for row in rows if row.add_player == "Andy Pages")
    assert dash.runner_up is None
    assert dash.bid == 1


def test_drop_without_player_link_keeps_name_only():
    rows = _parse()
    no_link = next(row for row in rows if row.faab_date == date(2026, 3, 22))
    assert no_link.drop_player == "Waiver Wire"
    assert no_link.drop_player_id is None
    assert no_link.add_player_id == 10231


def test_league_id_mismatch_raises():
    with pytest.raises(ClaimsParseError, match="league_id=1828"):
        _parse(league_id=1055)


def test_write_csv_and_cli(tmp_path: Path):
    rows = _parse()
    out = tmp_path / "claims_1828.csv"
    write_claims_csv(out, rows)
    with out.open(encoding="utf-8", newline="") as handle:
        table = list(csv.DictReader(handle))
    assert [row["add_player"] for row in table] == [
        "Michael Soroka",
        "Andy Pages",
        "Jazz Chisholm Jr.",
    ]
    assert table[1]["runner_up"] == ""
    assert table[0]["faab_date"] == "2026-09-20"

    cli_out = tmp_path / "from_cli.csv"
    rc = main(
        [
            "--html",
            str(FIXTURE),
            "--league-id",
            "1828",
            "--output",
            str(cli_out),
        ]
    )
    assert rc == 0
    assert cli_out.is_file()
    assert "Michael Soroka" in cli_out.read_text(encoding="utf-8")


def test_claims_page_url_is_league_scoped():
    assert claims_page_url(1828) == "https://nfc.shgn.com/claims?league_id=1828"


def test_cookies_from_env_are_optional_and_normalized():
    assert cookies_from_env({}) is None
    auth = cookies_from_env({"NFBC_LIU": "liu=abc", "NFBC_JWT": "jwt=def"})
    assert auth is not None
    assert auth.liu == "abc"
    assert auth.jwt == "def"
    names = {cookie["name"] for cookie in playwright_cookie_list(auth)}
    assert names == {"liu", "jwt"}
    assert all(cookie["domain"] == "nfc.shgn.com" for cookie in playwright_cookie_list(auth))


def test_cloudflare_html_is_detected():
    assert _page_looks_challenged("<html>__cf_chl</html>", None)
    assert _page_looks_challenged("<html></html>", "Just a moment...")
    assert not _page_looks_challenged("<table class='claims'></table>", "FAAB Results")


def test_load_claims_html_reads_saved_file():
    args = parse_args(["--html", str(FIXTURE), "--league-id", "1828"])
    html = load_claims_html(args)
    assert "FAAB Winning Bids" in html


def test_browser_cli_uses_fetch_hook(tmp_path: Path, monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")

    def fake_fetch(league_id, **kwargs):
        assert league_id == 1828
        assert kwargs["headed"] is True
        return html

    monkeypatch.setattr("nfbc_claims.fetch_claims_html_with_browser", fake_fetch)
    out = tmp_path / "claims_1828.csv"
    rc = main(["--output", str(out)])
    assert rc == 0
    assert "Michael Soroka" in out.read_text(encoding="utf-8")


def test_missing_html_file_is_a_fetch_error():
    args = parse_args(["--html", "/no/such/claims.html"])
    with pytest.raises(ClaimsFetchError, match="not found"):
        load_claims_html(args)
