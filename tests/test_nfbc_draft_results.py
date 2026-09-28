"""Local NFBC draft-results HTML parser and S3/--all ingest (#299)."""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nfbc_draft_results import (  # noqa: E402
    DraftFetchError,
    DraftLeague,
    DraftParseError,
    DraftUploadError,
    classify_draft_option,
    cookies_from_env,
    date_partition_path,
    delete_local_draft_csv,
    draft_s3_uri,
    fetch_draft_results_html,
    is_safe_draft_csv_path,
    load_draft_html,
    main,
    parse_args,
    parse_draft_league_index,
    parse_draft_results_html,
    process_league,
    run_all_leagues,
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


def test_cli_defaults_to_nolen_oc():
    args = parse_args([])
    assert args.league_id == 1828
    assert args.format == "online_championship"
    assert args.all is False
    assert args.s3 is False


def test_missing_html_and_cookies_is_a_fetch_error():
    args = parse_args(["--league-id", "1055"])
    with pytest.raises(DraftFetchError, match="NFBC_LIU"):
        load_draft_html(args)


def test_classify_excludes_qualifiers_and_cash():
    assert classify_draft_option("Main Event  Online #1055") == "main_event"
    assert classify_draft_option(
        "Nolen OC ($350 Rotowire Online Championship #1828)"
    ) == "online_championship"
    assert classify_draft_option("$125 Main Event Qualifier #295") is None
    assert classify_draft_option("Main Event Qualifier #295") is None
    assert classify_draft_option("$150 Cash League (12 Teams) #1395") is None


def test_dropdown_index_keeps_only_me_and_oc():
    leagues = parse_draft_league_index(FIXTURE.read_text(encoding="utf-8"))
    by_id = {item.league_id: item.format for item in leagues}
    assert by_id == {
        1828: "online_championship",
        1055: "main_event",
        217: "online_championship",
    }


def test_s3_uri_uses_format_and_date_partition():
    when = date(2026, 9, 28)
    assert date_partition_path(when) == "year=2026/month=09/day=28"
    assert (
        draft_s3_uri(
            "s3://dn-lakehouse-dev/nfbc/draft-results",
            "online_championship",
            1828,
            when=when,
        )
        == (
            "s3://dn-lakehouse-dev/nfbc/draft-results/online_championship/"
            "year=2026/month=09/day=28/draft_1828.csv"
        )
    )
    assert (
        draft_s3_uri(
            "s3://dn-lakehouse-dev/nfbc/draft-results", "main_event", 1055, when=when
        )
        == (
            "s3://dn-lakehouse-dev/nfbc/draft-results/main_event/"
            "year=2026/month=09/day=28/draft_1055.csv"
        )
    )


def test_safe_delete_only_removes_matching_draft_csv(tmp_path: Path):
    target = tmp_path / "draft_1828.csv"
    neighbor = tmp_path / "draft_1055.csv"
    other = tmp_path / "notes.txt"
    target.write_text("ok", encoding="utf-8")
    neighbor.write_text("keep", encoding="utf-8")
    other.write_text("keep", encoding="utf-8")
    assert is_safe_draft_csv_path(target, league_id=1828)
    assert not is_safe_draft_csv_path(neighbor, league_id=1828)
    assert not is_safe_draft_csv_path(other, league_id=1828)
    delete_local_draft_csv(target, league_id=1828)
    assert not target.exists()
    assert neighbor.read_text(encoding="utf-8") == "keep"
    assert other.read_text(encoding="utf-8") == "keep"
    with pytest.raises(DraftUploadError, match="Refusing to delete"):
        delete_local_draft_csv(neighbor, league_id=1828)


def test_safe_delete_refuses_symlink(tmp_path: Path):
    real = tmp_path / "secrets.txt"
    link = tmp_path / "draft_1828.csv"
    real.write_text("secret", encoding="utf-8")
    link.symlink_to(real)
    with pytest.raises(DraftUploadError, match="Refusing to delete"):
        delete_local_draft_csv(link, league_id=1828)
    assert real.exists()
    assert link.exists()


def test_process_league_uploads_then_deletes_only_that_csv(tmp_path: Path):
    html = FIXTURE.read_text(encoding="utf-8")
    out = tmp_path / "draft_1828.csv"
    neighbor = tmp_path / "draft_1055.csv"
    neighbor.write_text("leave me", encoding="utf-8")
    puts: list[tuple[str, str]] = []

    class FakeS3:
        def put_object(self, *, Bucket, Key, Body, ContentType):
            puts.append((Bucket, Key))
            assert ContentType == "text/csv"
            assert b"Ohtani, Shohei" in Body

    uri = process_league(
        html,
        league=DraftLeague(1828, "online_championship", "Nolen OC"),
        season=2026,
        output=out,
        s3=True,
        s3_base="s3://dn-lakehouse-dev/nfbc/draft-results",
        keep_local=False,
        s3_client=FakeS3(),
        when=date(2026, 9, 28),
    )
    assert uri == (
        "s3://dn-lakehouse-dev/nfbc/draft-results/online_championship/"
        "year=2026/month=09/day=28/draft_1828.csv"
    )
    assert puts == [
        (
            "dn-lakehouse-dev",
            "nfbc/draft-results/online_championship/year=2026/month=09/day=28/draft_1828.csv",
        )
    ]
    assert not out.exists()
    assert neighbor.read_text(encoding="utf-8") == "leave me"


def test_process_league_keep_local_skips_delete(tmp_path: Path):
    html = FIXTURE.read_text(encoding="utf-8")
    out = tmp_path / "draft_1828.csv"

    class FakeS3:
        def put_object(self, **kwargs):
            return None

    process_league(
        html,
        league=DraftLeague(1828, "online_championship", "Nolen OC"),
        season=2026,
        output=out,
        s3=True,
        s3_base="s3://dn-lakehouse-dev/nfbc/draft-results",
        keep_local=True,
        s3_client=FakeS3(),
    )
    assert out.is_file()


def test_failed_upload_keeps_local_csv(tmp_path: Path):
    html = FIXTURE.read_text(encoding="utf-8")
    out = tmp_path / "draft_1828.csv"

    class FakeS3:
        def put_object(self, **kwargs):
            raise RuntimeError("nope")

    with pytest.raises(DraftUploadError, match="S3 put failed"):
        process_league(
            html,
            league=DraftLeague(1828, "online_championship", "Nolen OC"),
            season=2026,
            output=out,
            s3=True,
            s3_base="s3://dn-lakehouse-dev/nfbc/draft-results",
            keep_local=False,
            s3_client=FakeS3(),
        )
    assert out.is_file()


def test_all_rejects_html_and_needs_cookies(monkeypatch):
    monkeypatch.delenv("NFBC_LIU", raising=False)
    monkeypatch.delenv("NFBC_JWT", raising=False)
    args = parse_args(["--all", "--html", str(FIXTURE)])
    with pytest.raises(DraftFetchError, match="not --html"):
        run_all_leagues(args, index_html="<html></html>")
    args = parse_args(["--all", "--pause-seconds", "0"])
    with pytest.raises(DraftFetchError, match="NFBC_LIU"):
        run_all_leagues(args, index_html="<html></html>")


def test_all_loop_uploads_each_league_and_deletes_locals(tmp_path: Path, monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")
    puts: list[str] = []

    class FakeS3:
        def put_object(self, *, Bucket, Key, Body, ContentType):
            puts.append(Key)

    monkeypatch.setattr(
        "nfbc_draft_results.default_output_path",
        lambda league_id: tmp_path / f"draft_{league_id}.csv",
    )
    monkeypatch.setattr(
        "nfbc_draft_results.date_partition_path",
        lambda when=None: "year=2026/month=09/day=28",
    )
    monkeypatch.chdir(tmp_path)
    args = parse_args(["--all", "--s3", "--pause-seconds", "0"])
    rc = run_all_leagues(
        args,
        s3_client=FakeS3(),
        index_html=html,
        fetch_html=lambda league_id: html,
    )
    assert rc == 0
    assert puts == [
        "nfbc/draft-results/online_championship/year=2026/month=09/day=28/draft_1828.csv",
        "nfbc/draft-results/main_event/year=2026/month=09/day=28/draft_1055.csv",
        "nfbc/draft-results/online_championship/year=2026/month=09/day=28/draft_217.csv",
    ]
    assert not (tmp_path / "draft_1828.csv").exists()
    assert not (tmp_path / "draft_1055.csv").exists()
    assert not (tmp_path / "draft_217.csv").exists()


def test_cloudflare_challenge_is_a_fetch_error(monkeypatch):
    response = MagicMock()
    response.read.return_value = b"<html>Just a moment</html>"
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    monkeypatch.setattr("nfbc_draft_results.urlopen", lambda *args, **kwargs: response)
    with pytest.raises(DraftFetchError, match="Cloudflare"):
        fetch_draft_results_html(1828, liu="abc")
