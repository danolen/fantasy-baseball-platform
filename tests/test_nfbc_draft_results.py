"""Local NFBC draft-results HTML parser (#299)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nfbc_draft_results import (  # noqa: E402
    DraftFetchError,
    DraftParseError,
    cookies_from_env,
    load_draft_html,
    main,
    parse_args,
    parse_draft_results_html,
    write_draft_csv,
)

FIXTURE = ROOT / "tests" / "fixtures" / "nfbc_draft_results_sample.html"


def _parse(html: str | None = None, **kwargs):
    text = html if html is not None else FIXTURE.read_text(encoding="utf-8")
    defaults = {
        "league_id": 1055,
        "format": "main_event",
        "season": 2026,
    }
    defaults.update(kwargs)
    return parse_draft_results_html(text, **defaults)


def test_fixture_parses_three_picks_without_auction_dollars():
    rows = _parse()
    assert len(rows) == 3
    first = rows[0]
    assert first.league_id == 1055
    assert first.format == "main_event"
    assert first.round == 1
    assert first.pick == 1
    assert first.player == "Ohtani, Shohei"
    assert first.player_id == 9810
    assert first.position == "UT"
    assert first.team == "The Franchise 22"
    assert first.team_id == 753794
    assert first.duration == "4 sec"
    assert not hasattr(first, "auction_dollars")
    last = rows[-1]
    assert last.round == 30
    assert last.pick == 450
    assert last.player_id == 10111


def test_write_csv_and_cli(tmp_path: Path):
    rows = _parse()
    out = tmp_path / "draft_1055.csv"
    write_draft_csv(out, rows)
    with out.open(encoding="utf-8", newline="") as handle:
        table = list(csv.DictReader(handle))
    assert [row["player"] for row in table] == [
        "Ohtani, Shohei",
        "Judge, Aaron",
        "Julien, Edouard",
    ]
    assert "auction" not in ",".join(table[0].keys()).lower()
    assert table[0]["pick"] == "1"
    assert table[2]["team_id"] == "753794"

    cli_out = tmp_path / "from_cli.csv"
    rc = main(
        [
            "--html",
            str(FIXTURE),
            "--league-id",
            "1055",
            "--format",
            "main_event",
            "--output",
            str(cli_out),
        ]
    )
    assert rc == 0
    assert "Ohtani, Shohei" in cli_out.read_text(encoding="utf-8")


def test_login_wall_is_a_fetch_error():
    with pytest.raises(DraftFetchError, match="login wall"):
        _parse("Must be logged in to view this page.")


def test_missing_table_raises():
    with pytest.raises(DraftParseError, match="tbl_draft_results"):
        _parse("<html><body>no table</body></html>")


def test_cookies_from_env_are_optional_and_normalized():
    assert cookies_from_env({}) is None
    auth = cookies_from_env({"NFBC_LIU": "liu=abc", "NFBC_JWT": "jwt=def"})
    assert auth == ("abc", "def")


def test_load_html_reads_saved_file():
    args = parse_args(["--html", str(FIXTURE), "--league-id", "1055"])
    html = load_draft_html(args)
    assert "tbl_draft_results" in html


def test_missing_html_and_cookies_is_a_fetch_error():
    args = parse_args(["--league-id", "1055"])
    with pytest.raises(DraftFetchError, match="NFBC_LIU"):
        load_draft_html(args)
