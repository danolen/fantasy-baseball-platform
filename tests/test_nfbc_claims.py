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
    ClaimsLeague,
    ClaimsParseError,
    ClaimsUploadError,
    chrome_debug_command,
    claims_page_url,
    claims_s3_uri,
    classify_claims_option,
    date_partition_path,
    cookies_from_env,
    delete_local_claims_csv,
    is_safe_claims_csv_path,
    load_claims_html,
    main,
    parse_args,
    parse_claims_html,
    parse_claims_league_index,
    parse_week_title,
    playwright_cookie_list,
    process_league,
    run_all_leagues,
    write_claims_csv,
    _cloudflare_help,
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


def test_connect_cdp_flag_is_passed_through(monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")
    seen: dict = {}

    def fake_fetch(league_id, **kwargs):
        seen.update(kwargs)
        seen["league_id"] = league_id
        return html

    monkeypatch.setattr("nfbc_claims.fetch_claims_html_with_browser", fake_fetch)
    args = parse_args(
        ["--connect-cdp", "http://127.0.0.1:9222", "--channel", "chrome"]
    )
    assert args.connect_cdp == "http://127.0.0.1:9222"
    assert args.channel == "chrome"
    load_claims_html(args)
    assert seen["connect_cdp"] == "http://127.0.0.1:9222"
    assert seen["channel"] == "chrome"
    assert seen["cookies"] is None


def test_cloudflare_help_points_at_connect_cdp():
    cmd = chrome_debug_command()
    assert "--remote-debugging-port=9222" in cmd
    help_text = _cloudflare_help(profile=Path("/tmp/profile"))
    assert "--connect-cdp" in help_text
    assert cmd.split()[0] in help_text or "Chrome" in help_text


def test_classify_excludes_qualifiers_and_cash():
    assert classify_claims_option("Main Event  Online #1055") == "main_event"
    assert classify_claims_option(
        "Nolen OC ($350 Rotowire Online Championship #1828)"
    ) == "online_championship"
    assert classify_claims_option("$125 Main Event Qualifier #295") is None
    assert classify_claims_option("$150 Cash League (12 Teams) #1395") is None


def test_dropdown_index_keeps_only_me_and_oc():
    leagues = parse_claims_league_index(FIXTURE.read_text(encoding="utf-8"))
    by_id = {item.league_id: item.format for item in leagues}
    assert by_id == {1828: "online_championship", 1055: "main_event", 217: "online_championship"}


def test_s3_uri_uses_format_and_date_partition():
    when = date(2026, 9, 27)
    assert date_partition_path(when) == "year=2026/month=09/day=27"
    assert (
        claims_s3_uri(
            "s3://dn-lakehouse-dev/nfbc/claims",
            "online_championship",
            1828,
            when=when,
        )
        == (
            "s3://dn-lakehouse-dev/nfbc/claims/online_championship/"
            "year=2026/month=09/day=27/claims_1828.csv"
        )
    )
    assert (
        claims_s3_uri(
            "s3://dn-lakehouse-dev/nfbc/claims", "main_event", 1055, when=when
        )
        == (
            "s3://dn-lakehouse-dev/nfbc/claims/main_event/"
            "year=2026/month=09/day=27/claims_1055.csv"
        )
    )


def test_safe_delete_only_removes_matching_claims_csv(tmp_path: Path):
    target = tmp_path / "claims_1828.csv"
    neighbor = tmp_path / "claims_1055.csv"
    other = tmp_path / "notes.txt"
    target.write_text("ok", encoding="utf-8")
    neighbor.write_text("keep", encoding="utf-8")
    other.write_text("keep", encoding="utf-8")
    assert is_safe_claims_csv_path(target, league_id=1828)
    assert not is_safe_claims_csv_path(neighbor, league_id=1828)
    assert not is_safe_claims_csv_path(other, league_id=1828)
    delete_local_claims_csv(target, league_id=1828)
    assert not target.exists()
    assert neighbor.read_text(encoding="utf-8") == "keep"
    assert other.read_text(encoding="utf-8") == "keep"
    with pytest.raises(ClaimsUploadError, match="Refusing to delete"):
        delete_local_claims_csv(neighbor, league_id=1828)


def test_safe_delete_refuses_symlink(tmp_path: Path):
    real = tmp_path / "secrets.txt"
    link = tmp_path / "claims_1828.csv"
    real.write_text("secret", encoding="utf-8")
    link.symlink_to(real)
    with pytest.raises(ClaimsUploadError, match="Refusing to delete"):
        delete_local_claims_csv(link, league_id=1828)
    assert real.exists()
    assert link.exists()


def test_process_league_uploads_then_deletes_only_that_csv(tmp_path: Path):
    html = FIXTURE.read_text(encoding="utf-8")
    out = tmp_path / "claims_1828.csv"
    neighbor = tmp_path / "claims_1055.csv"
    neighbor.write_text("leave me", encoding="utf-8")
    puts: list[tuple[str, str]] = []

    class FakeS3:
        def put_object(self, *, Bucket, Key, Body, ContentType):
            puts.append((Bucket, Key))
            assert ContentType == "text/csv"
            assert b"Michael Soroka" in Body

    uri = process_league(
        html,
        league=ClaimsLeague(1828, "online_championship", "Nolen OC"),
        season=2026,
        output=out,
        s3=True,
        s3_base="s3://dn-lakehouse-dev/nfbc/claims",
        keep_local=False,
        s3_client=FakeS3(),
        when=date(2026, 9, 27),
    )
    assert uri == (
        "s3://dn-lakehouse-dev/nfbc/claims/online_championship/"
        "year=2026/month=09/day=27/claims_1828.csv"
    )
    assert puts == [
        (
            "dn-lakehouse-dev",
            "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv",
        )
    ]
    assert not out.exists()
    assert neighbor.read_text(encoding="utf-8") == "leave me"


def test_process_league_keep_local_skips_delete(tmp_path: Path):
    html = FIXTURE.read_text(encoding="utf-8")
    out = tmp_path / "claims_1828.csv"

    class FakeS3:
        def put_object(self, **kwargs):
            return None

    process_league(
        html,
        league=ClaimsLeague(1828, "online_championship", "Nolen OC"),
        season=2026,
        output=out,
        s3=True,
        s3_base="s3://dn-lakehouse-dev/nfbc/claims",
        keep_local=True,
        s3_client=FakeS3(),
    )
    assert out.is_file()


def test_failed_upload_keeps_local_csv(tmp_path: Path):
    html = FIXTURE.read_text(encoding="utf-8")
    out = tmp_path / "claims_1828.csv"

    class FakeS3:
        def put_object(self, **kwargs):
            raise RuntimeError("nope")

    with pytest.raises(ClaimsUploadError, match="S3 put failed"):
        process_league(
            html,
            league=ClaimsLeague(1828, "online_championship", "Nolen OC"),
            season=2026,
            output=out,
            s3=True,
            s3_base="s3://dn-lakehouse-dev/nfbc/claims",
            keep_local=False,
            s3_client=FakeS3(),
        )
    assert out.is_file()


def test_all_loop_uploads_each_league_and_deletes_locals(tmp_path: Path, monkeypatch):
    base = FIXTURE.read_text(encoding="utf-8")
    puts: list[str] = []

    def html_for(league_id: int) -> str:
        text = base.replace(" selected", "")
        return text.replace(f'value="{league_id}"', f'value="{league_id}" selected', 1)

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def fetch(self, league_id):
            return html_for(league_id)

    class FakeS3:
        def put_object(self, *, Bucket, Key, Body, ContentType):
            puts.append(Key)

    monkeypatch.setattr("nfbc_claims.ClaimsBrowser", lambda **kwargs: FakeBrowser())
    monkeypatch.setattr(
        "nfbc_claims.default_output_path",
        lambda league_id: tmp_path / f"claims_{league_id}.csv",
    )
    monkeypatch.setattr(
        "nfbc_claims.date_partition_path",
        lambda when=None: "year=2026/month=09/day=27",
    )
    monkeypatch.chdir(tmp_path)
    args = parse_args(
        [
            "--all",
            "--s3",
            "--connect-cdp",
            "http://127.0.0.1:9222",
            "--pause-seconds",
            "0",
        ]
    )
    rc = run_all_leagues(args, s3_client=FakeS3())
    assert rc == 0
    assert puts == [
        "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv",
        "nfbc/claims/main_event/year=2026/month=09/day=27/claims_1055.csv",
        "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_217.csv",
    ]
    assert not (tmp_path / "claims_1828.csv").exists()
    assert not (tmp_path / "claims_1055.csv").exists()
    assert not (tmp_path / "claims_217.csv").exists()
