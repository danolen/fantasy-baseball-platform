"""Synthetic coverage for FAAB what-if (#187)."""

from __future__ import annotations

import pytest

from faab_what_if import (
    RANK_MODE_OVERALL,
    RANK_MODE_WEEKLY,
    RETAIN_PROTECT,
    RETAIN_STREAM,
    UNCERTAINTY_CLEAR,
    UNCERTAINTY_WITHIN_NOISE,
    analyze_add_drop,
    classify_interim_retention,
    compute_category_deltas,
    format_delta_rows,
    format_projected_stats,
    rank_candidates,
    starters_table,
    suggest_drop,
)


def hitter(nfbc_id, pos, dollars, **extra):
    player = {
        "nfbc_id": nfbc_id,
        "player_name": f"H{nfbc_id}",
        "row_type": "hitter",
        "pos_raw": ",".join(pos),
        "pos_array": list(pos),
        "dollars": dollars,
        "dollars_monday_thursday": dollars,
        "dollars_friday_sunday": dollars,
    }
    player.update(extra)
    return player


def pitcher(nfbc_id, dollars, **extra):
    player = {
        "nfbc_id": nfbc_id,
        "player_name": f"P{nfbc_id}",
        "row_type": "pitcher",
        "pos_raw": "SP",
        "pos_array": ["SP"],
        "dollars": dollars,
    }
    player.update(extra)
    return player


def plan_row(category, *, pts_per=1.0, noise=1.0, is_ratio=False, unit=1.0):
    return {
        "category": category,
        "is_ratio": is_ratio,
        "higher_is_better": category not in ("ERA", "WHIP"),
        "overall_points_per_raw_unit": pts_per,
        "noise_floor_raw": noise,
        "raw_unit_size": unit,
        "tie_cluster_raw_width": 0.0,
    }


FULL_PLAN = [
    plan_row("R", pts_per=2.0, noise=1.0),
    plan_row("HR", pts_per=5.0, noise=1.0),
    plan_row("RBI", pts_per=2.0, noise=1.0),
    plan_row("SB", pts_per=8.0, noise=1.0),
    plan_row("AVG", pts_per=20.0, noise=0.001, is_ratio=True, unit=0.001),
    plan_row("K", pts_per=1.0, noise=1.0),
    plan_row("W", pts_per=10.0, noise=1.0),
    plan_row("SV", pts_per=15.0, noise=1.0),
    plan_row("ERA", pts_per=10.0, noise=0.01, is_ratio=True, unit=0.01),
    plan_row("WHIP", pts_per=20.0, noise=0.005, is_ratio=True, unit=0.005),
]


# ---------------------------------------------------------------------------
# Acceptance: inspectable lineups + direct upgrade
# ---------------------------------------------------------------------------


def test_direct_upgrade_is_inspectable():
    roster = [
        hitter(1, ["OF"], 10.0, r=2.0, hr=0.0, rbi=2.0, sb=0.0, hits=5.0, ab=20.0),
    ]
    add = hitter(2, ["OF"], 40.0, r=5.0, hr=2.0, rbi=6.0, sb=1.0, hits=8.0, ab=20.0)

    result = analyze_add_drop(
        roster,
        {"OF": 1},
        add=add,
        drop_key=(1, "hitter"),
        auto_suggest_drop=False,
        plan_rows=FULL_PLAN,
    )
    assert result.ok
    assert result.baseline is not None and result.what_if is not None
    assert result.baseline.starter_ids() == {1}
    assert result.what_if.starter_ids() == {2}
    assert result.net_weekly_value == pytest.approx(60.0)  # Mon–Thu + Fri–Sun

    base_tbl = starters_table(result.baseline)
    what_tbl = starters_table(result.what_if)
    assert base_tbl[0]["nfbc_id"] == 1
    assert what_tbl[0]["nfbc_id"] == 2

    by_cat = {d.category: d for d in result.category_deltas}
    assert by_cat["R"].delta_raw == pytest.approx(3.0)
    assert by_cat["HR"].delta_raw == pytest.approx(2.0)
    assert by_cat["R"].uncertainty == UNCERTAINTY_CLEAR
    assert result.net_overall_pts_estimate is not None
    assert result.net_overall_pts_estimate > 0


def test_bench_only_acquisition_zero_immediate_impact():
    roster = [
        hitter(1, ["OF"], 30.0, r=4.0, hr=1.0, rbi=4.0, sb=1.0, hits=6.0, ab=20.0),
        hitter(2, ["OF"], 20.0, r=2.0, hr=0.0, rbi=2.0, sb=0.0, hits=4.0, ab=20.0),
    ]
    add = hitter(3, ["OF"], 1.0, r=99.0, hr=99.0, rbi=99.0, sb=99.0, hits=99.0, ab=20.0)

    result = analyze_add_drop(
        roster,
        {"OF": 1},
        add=add,
        drop_key=(2, "hitter"),
        auto_suggest_drop=False,
        plan_rows=FULL_PLAN,
    )
    assert result.ok
    assert result.bench_only_add
    assert result.net_weekly_value == pytest.approx(0.0)
    assert result.baseline.starter_ids() == result.what_if.starter_ids() == {1}
    for d in result.category_deltas:
        if d.delta_raw is not None:
            assert d.delta_raw == pytest.approx(0.0)


def test_position_constraint_blocks_higher_value_add():
    roster = [hitter(1, ["C"], 5.0, r=1.0, hr=0.0, rbi=1.0, sb=0.0, hits=2.0, ab=10.0)]
    add = hitter(2, ["OF"], 99.0, r=10.0, hr=5.0, rbi=10.0, sb=5.0, hits=10.0, ab=20.0)

    result = analyze_add_drop(
        roster,
        {"C": 1},
        add=add,
        auto_suggest_drop=False,  # no drop — roster has room conceptually but C-only
        drop_key=None,
        plan_rows=FULL_PLAN,
    )
    # Without a drop, add sits beside the C; still cannot fill C, so starters unchanged.
    assert result.ok
    assert result.what_if.starter_ids() == {1}
    assert result.net_weekly_value == pytest.approx(0.0)


def test_ratio_tradeoff_uses_aggregate_numerators():
    """High-AVG low-counting add displaces a counting bat — AVG up, R down."""
    slugger = hitter(
        1, ["OF"], 20.0, r=6.0, hr=2.0, rbi=6.0, sb=0.0, hits=4.0, ab=24.0  # .167
    )
    contact = hitter(
        2, ["OF"], 18.0, r=2.0, hr=0.0, rbi=1.0, sb=0.0, hits=10.0, ab=20.0  # .500
    )
    # Need a second OF slot holder so drop is meaningful
    filler = hitter(
        3, ["OF"], 5.0, r=1.0, hr=0.0, rbi=1.0, sb=0.0, hits=3.0, ab=15.0
    )

    # Baseline: start slugger + filler (higher $). What-if: drop filler, add contact
    # — contact out-earns filler but may or may not beat slugger.
    # Force: OF:1 only, drop slugger for contact.
    result = analyze_add_drop(
        [slugger],
        {"OF": 1},
        add=contact,
        drop_key=(1, "hitter"),
        auto_suggest_drop=False,
        plan_rows=FULL_PLAN,
    )
    assert result.ok
    assert result.what_if.starter_ids() == {2}

    by_cat = {d.category: d for d in result.category_deltas}
    assert by_cat["R"].delta_raw == pytest.approx(-4.0)
    assert by_cat["AVG"].baseline == pytest.approx(4.0 / 24.0)
    assert by_cat["AVG"].what_if == pytest.approx(10.0 / 20.0)
    assert by_cat["AVG"].delta_raw == pytest.approx(10.0 / 20.0 - 4.0 / 24.0)

    # Confirm engine aggregates match (not mean of player AVGs).
    assert result.what_if.totals["avg"] == pytest.approx(10.0 / 20.0)


def test_unmatched_candidate_warns_clearly():
    roster = [hitter(1, ["OF"], 10.0)]
    result = analyze_add_drop(
        roster,
        {"OF": 1},
        add_nfbc_id=999,
        free_agents=[],
        auto_suggest_drop=False,
        drop_key=(1, "hitter"),
    )
    assert not result.ok
    assert "unmatched" in result.message.lower() or "not found" in result.message.lower()


def test_rank_switches_between_weekly_and_team_fit():
    """High-$ streamer vs lower-$ steal specialist under SB-heavy plan."""
    roster = [
        hitter(1, ["OF"], 15.0, r=3.0, hr=1.0, rbi=3.0, sb=0.0, hits=5.0, ab=20.0),
        hitter(2, ["OF"], 5.0, r=1.0, hr=0.0, rbi=1.0, sb=0.0, hits=2.0, ab=10.0),
    ]
    # Held AVG-neutral (same hits/ab) so SB is the only ratio-free differentiator.
    # Correctly scaled AVG is worth 20 pts per 0.001, which would otherwise swamp
    # the SB weight this test is exercising.
    # Candidate A: big weekly $, no SB
    a = hitter(10, ["OF"], 25.0, r=4.0, hr=2.0, rbi=5.0, sb=0.0, hits=5.0, ab=20.0)
    # Candidate B: lower $, lots of SB (team-fit when SB pts/unit is huge)
    b = hitter(11, ["OF"], 16.0, r=2.0, hr=0.0, rbi=2.0, sb=3.0, hits=5.0, ab=20.0)

    sb_heavy = []
    for r in FULL_PLAN:
        row = dict(r)
        if row["category"] == "SB":
            row["overall_points_per_raw_unit"] = 50.0
        sb_heavy.append(row)

    weekly = rank_candidates(
        roster,
        {"OF": 1},
        [10, 11],
        free_agents=[a, b],
        drop_key=(2, "hitter"),
        auto_suggest_drop=False,
        rank_mode=RANK_MODE_WEEKLY,
        plan_rows=sb_heavy,
    )
    overall = rank_candidates(
        roster,
        {"OF": 1},
        [10, 11],
        free_agents=[a, b],
        drop_key=(2, "hitter"),
        auto_suggest_drop=False,
        rank_mode=RANK_MODE_OVERALL,
        plan_rows=sb_heavy,
    )

    assert weekly[0]["add_nfbc_id"] == 10  # higher net weekly $
    assert overall[0]["add_nfbc_id"] == 11  # SB-driven team fit


def test_candidates_within_noise_are_tied_not_ranked():
    roster = [
        hitter(1, ["OF"], 10.0, r=2.0, hr=0.0, rbi=2.0, sb=0.0, hits=4.0, ab=20.0),
        hitter(2, ["OF"], 1.0, r=0.0, hr=0.0, rbi=0.0, sb=0.0, hits=1.0, ab=10.0),
    ]
    # Counting-only plan so the tie threshold is 1 R × 2 pts = 2 overall pts.
    # A 0.1 R gap (0.2 overall pts) must present as tied, not ranked.
    counting_plan = [
        plan_row("R", pts_per=2.0, noise=1.0),
        plan_row("HR", pts_per=5.0, noise=1.0),
        plan_row("RBI", pts_per=2.0, noise=1.0),
        plan_row("SB", pts_per=8.0, noise=1.0),
    ]
    c1 = hitter(10, ["OF"], 12.0, r=3.0, hr=0.0, rbi=2.0, sb=0.0, hits=5.0, ab=20.0)
    c2 = hitter(11, ["OF"], 12.0, r=3.1, hr=0.0, rbi=2.0, sb=0.0, hits=5.0, ab=20.0)

    ranked = rank_candidates(
        roster,
        {"OF": 1},
        [10, 11],
        free_agents=[c1, c2],
        drop_key=(2, "hitter"),
        auto_suggest_drop=False,
        rank_mode=RANK_MODE_OVERALL,
        plan_rows=counting_plan,
    )
    assert ranked[0]["ok"] and ranked[1]["ok"]
    assert ranked[0]["display_rank"] == ranked[1]["display_rank"] == 1
    assert ranked[0]["tied"] and ranked[1]["tied"]


def test_net_delta_carries_uncertainty_indication():
    baseline = {"r": 5.0, "hr": 1.0, "rbi": 5.0, "sb": 1.0, "hits": 10.0, "ab": 40.0,
                "avg": 0.250, "k": 0.0, "w": 0.0, "sv": 0.0,
                "ip": 0.0, "er": 0.0, "hits_allowed": 0.0, "walks_allowed": 0.0,
                "era": None, "whip": None}
    # +0.2 R is inside a 1.0 noise floor
    what_if = dict(baseline, r=5.2)
    deltas, net, any_within = compute_category_deltas(baseline, what_if, FULL_PLAN)
    by_cat = {d.category: d for d in deltas}
    assert by_cat["R"].uncertainty == UNCERTAINTY_WITHIN_NOISE
    assert by_cat["R"].delta_overall_pts_estimate == pytest.approx(0.4)
    assert any_within

    what_if_big = dict(baseline, r=8.0)
    deltas2, _, _ = compute_category_deltas(baseline, what_if_big, FULL_PLAN)
    assert {d.category: d for d in deltas2}["R"].uncertainty == UNCERTAINTY_CLEAR

    table = format_delta_rows(deltas)
    assert "uncertainty" in table[0]


def test_ratio_deltas_scale_by_raw_unit_size():
    """ERA/WHIP points are priced per 0.01 / 0.005, not per 1.0 of the rate.

    Multiplying the raw delta directly understated ERA by 100x and WHIP by
    200x, which made a blowup start look nearly free next to a win.
    """
    baseline = {"r": 0.0, "hr": 0.0, "rbi": 0.0, "sb": 0.0, "hits": 0.0, "ab": 0.0,
                "avg": None, "k": 0.0, "w": 0.0, "sv": 0.0,
                "ip": 100.0, "er": 40.0, "hits_allowed": 90.0, "walks_allowed": 30.0,
                "era": 3.60, "whip": 1.20}
    # 5 IP / 6 ER / 10 baserunners tacked on: ERA 3.60 -> 3.9429, WHIP 1.20 -> 1.2381
    what_if = dict(baseline, ip=105.0, er=46.0, hits_allowed=97.0,
                   walks_allowed=33.0, era=46.0 * 9 / 105.0, whip=130.0 / 105.0)
    deltas, net, _ = compute_category_deltas(baseline, what_if, FULL_PLAN)
    by_cat = {d.category: d for d in deltas}

    era_delta = by_cat["ERA"].delta_raw
    whip_delta = by_cat["WHIP"].delta_raw
    assert era_delta == pytest.approx(0.342857, abs=1e-5)
    assert whip_delta == pytest.approx(0.038095, abs=1e-5)

    # ERA pts_per=10.0 per 0.01 unit -> 0.342857 / 0.01 * 10 = 342.86, and worse
    # ERA is a loss, so the estimate is negative. The old code returned -3.43.
    assert by_cat["ERA"].delta_overall_pts_estimate == pytest.approx(-342.86, abs=0.1)
    # WHIP pts_per=20.0 per 0.005 unit -> 0.038095 / 0.005 * 20 = 152.38 (was -0.76)
    assert by_cat["WHIP"].delta_overall_pts_estimate == pytest.approx(-152.38, abs=0.1)
    assert net == pytest.approx(-342.86 - 152.38, abs=0.5)


def test_ratio_delta_dwarfs_a_single_win():
    """Guards the decision this bug corrupted: ratio damage vs one win."""
    baseline = {"r": 0.0, "hr": 0.0, "rbi": 0.0, "sb": 0.0, "hits": 0.0, "ab": 0.0,
                "avg": None, "k": 0.0, "w": 0.0, "sv": 0.0,
                "ip": 100.0, "er": 40.0, "hits_allowed": 90.0, "walks_allowed": 30.0,
                "era": 3.60, "whip": 1.20}
    # A win plus a blowup that inflates ERA/WHIP.
    what_if = dict(baseline, w=1.0, ip=104.0, er=48.0, hits_allowed=98.0,
                   walks_allowed=34.0, era=48.0 * 9 / 104.0, whip=132.0 / 104.0)
    _, net, _ = compute_category_deltas(baseline, what_if, FULL_PLAN)
    # W is 10 pts/win in the fixture; the ratio hit must dominate it.
    assert net is not None and net < -100.0


def test_rank_unmatched_warning():
    roster = [hitter(1, ["OF"], 10.0)]
    ranked = rank_candidates(
        roster,
        {"OF": 1},
        [999],
        free_agents=[],
        auto_suggest_drop=False,
        drop_key=(1, "hitter"),
        rank_mode=RANK_MODE_WEEKLY,
    )
    assert ranked[0]["ok"] is False
    assert "unmatched" in ranked[0]["message"]
    assert "_unmatched_warning" in ranked[0]


def test_auto_suggest_drop_picks_bench():
    roster = [
        hitter(1, ["OF"], 20.0, r=4.0, hits=6.0, ab=20.0),
        hitter(2, ["OF"], 2.0, r=1.0, hits=2.0, ab=10.0),
    ]
    add = hitter(3, ["OF"], 35.0, r=5.0, hits=7.0, ab=20.0)
    result = analyze_add_drop(
        roster, {"OF": 1}, add=add, auto_suggest_drop=True, plan_rows=FULL_PLAN
    )
    assert result.ok
    assert result.drop_suggested
    assert result.drop_nfbc_id == 2
    assert result.what_if.starter_ids() == {3}


def test_monday_only_start_uses_weekend_bat_for_friday():
    """Pickup strong Mon–Thu / weak weekend → weekend starter stays the other bat."""
    roster = [
        hitter(
            1,
            ["OF"],
            10.0,
            dollars_monday_thursday=8.0,
            dollars_friday_sunday=20.0,
            r=4.0,
            hr=1.0,
            rbi=4.0,
            sb=0.0,
            hits=6.0,
            ab=20.0,
            mt_r=1.0,
            mt_hr=0.0,
            mt_rbi=1.0,
            mt_sb=0.0,
            mt_hits=2.0,
            mt_ab=8.0,
            fs_r=3.0,
            fs_hr=1.0,
            fs_rbi=3.0,
            fs_sb=0.0,
            fs_hits=4.0,
            fs_ab=12.0,
        ),
        hitter(
            2,
            ["OF"],
            1.0,
            dollars_monday_thursday=1.0,
            dollars_friday_sunday=1.0,
            r=0.0,
            hits=0.0,
            ab=5.0,
            mt_r=0.0,
            mt_hits=0.0,
            mt_ab=2.0,
            fs_r=0.0,
            fs_hits=0.0,
            fs_ab=3.0,
        ),
    ]
    add = hitter(
        10,
        ["OF"],
        15.0,
        dollars_monday_thursday=25.0,
        dollars_friday_sunday=2.0,
        r=5.0,
        hr=2.0,
        rbi=5.0,
        sb=1.0,
        hits=8.0,
        ab=20.0,
        mt_r=4.0,
        mt_hr=2.0,
        mt_rbi=4.0,
        mt_sb=1.0,
        mt_hits=6.0,
        mt_ab=12.0,
        fs_r=1.0,
        fs_hr=0.0,
        fs_rbi=1.0,
        fs_sb=0.0,
        fs_hits=2.0,
        fs_ab=8.0,
    )

    result = analyze_add_drop(
        roster,
        {"OF": 1},
        add=add,
        drop_key=(2, "hitter"),
        auto_suggest_drop=False,
        plan_rows=FULL_PLAN,
    )
    assert result.ok
    assert result.what_if.starter_ids() == {10}  # Monday
    assert result.what_if_friday is not None
    assert result.what_if_friday.starter_ids() == {1}  # Friday swaps back
    assert result.starts_monday_only

    by_cat = {d.category: d for d in result.category_deltas}
    # Baseline: player 1 both halves → mt + fs R = 1+3 = 4
    # What-if: add Mon (4 R) + player 1 Fri (3 R) = 7
    assert by_cat["R"].baseline == pytest.approx(4.0)
    assert by_cat["R"].what_if == pytest.approx(7.0)
    assert by_cat["R"].delta_raw == pytest.approx(3.0)


def test_format_projected_stats_hitter_uses_counting_and_avg():
    line = format_projected_stats(
        hitter(
            1,
            ["OF"],
            10.0,
            r=3.2,
            hr=1.1,
            rbi=3.4,
            sb=0.4,
            hits=6.0,
            ab=21.0,
        )
    )
    assert line == "3.2 R · 1.1 HR · 3.4 RBI · 0.4 SB · .286 AVG"


def test_format_projected_stats_pitcher_and_reliever():
    starter = format_projected_stats(
        pitcher(
            2,
            12.0,
            gs=2,
            ip=10.9,
            w=0.7,
            sv=0.0,
            k=11.4,
            era=3.62,
            whip=1.19,
        )
    )
    assert starter == "2 GS · 10.9 IP · 0.7 W · 11.4 K · 3.62 ERA · 1.19 WHIP"
    assert "SV" not in starter

    reliever = format_projected_stats(
        pitcher(3, 8.0, gs=0, ip=2.8, w=0.1, sv=1.3, k=3.2, era=2.90, whip=1.07)
    )
    assert reliever == "2.8 IP · 0.1 W · 1.3 SV · 3.2 K · 2.90 ERA · 1.07 WHIP"
    assert "GS" not in reliever


def test_rank_candidates_includes_projected_stats():
    roster = [hitter(1, ["OF"], 5.0, r=1.0, hr=0.0, rbi=1.0, sb=0.0, hits=2.0, ab=10.0)]
    add = hitter(
        10,
        ["OF"],
        20.0,
        r=4.0,
        hr=2,
        rbi=5.0,
        sb=1.0,
        batting_avg=0.310,
    )
    ranked = rank_candidates(
        roster,
        {"OF": 1},
        [10, 999],
        free_agents=[add],
        drop_key=(1, "hitter"),
        auto_suggest_drop=False,
        rank_mode=RANK_MODE_WEEKLY,
    )
    by_id = {str(r["add_nfbc_id"]): r for r in ranked}
    assert by_id["10"]["projected_stats"] == "4 R · 2 HR · 5 RBI · 1 SB · .310 AVG"
    assert by_id["999"]["projected_stats"] == ""


def test_format_projected_stats_pitcher_without_row_type_uses_pos():
    p = pitcher(
        2,
        12.0,
        gs=2,
        ip=10.9,
        w=0.7,
        sv=0.0,
        k=11.4,
        era=3.62,
        whip=1.19,
    )
    p.pop("row_type")
    line = format_projected_stats(p)
    assert line == "2 GS · 10.9 IP · 0.7 W · 11.4 K · 3.62 ERA · 1.19 WHIP"


def test_format_projected_stats_hitter_uppercase_keys():
    line = format_projected_stats(
        {
            "row_type": "HITTER",
            "R": 2.0,
            "HR": 0.9,
            "RBI": 2.1,
            "SB": 0.2,
            "AVG": 0.184,
        }
    )
    assert line == "2 R · 0.9 HR · 2.1 RBI · 0.2 SB · .184 AVG"


def test_rank_candidates_matches_float_nfbc_id_to_string_candidate():
    roster = [hitter(1, ["OF"], 5.0, r=1.0, hr=0.0, rbi=1.0, sb=0.0, hits=2.0, ab=10.0)]
    add = hitter(
        10,
        ["OF"],
        20.0,
        r=4.0,
        hr=2,
        rbi=5.0,
        sb=1.0,
        batting_avg=0.310,
    )
    add["nfbc_id"] = 10.0
    ranked = rank_candidates(
        roster,
        {"OF": 1},
        ["10"],
        free_agents=[add],
        drop_key=(1, "hitter"),
        auto_suggest_drop=False,
        rank_mode=RANK_MODE_WEEKLY,
    )
    assert ranked[0]["ok"] is True
    assert ranked[0]["projected_stats"] == "4 R · 2 HR · 5 RBI · 1 SB · .310 AVG"


def test_rank_view_projected_column_stays_string():
    import pandas as pd

    roster = [hitter(1, ["OF"], 5.0, r=1.0, hr=0.0, rbi=1.0, sb=0.0, hits=2.0, ab=10.0)]
    add = hitter(
        10,
        ["OF"],
        20.0,
        r=4.0,
        hr=2,
        rbi=5.0,
        sb=1.0,
        batting_avg=0.310,
    )
    ranked = rank_candidates(
        roster,
        {"OF": 1},
        [10],
        free_agents=[add],
        drop_key=(1, "hitter"),
        auto_suggest_drop=False,
        rank_mode=RANK_MODE_WEEKLY,
    )
    view = pd.DataFrame(
        [
            {
                "Add": "H10 (OF)",
                "Projected": r.get("projected_stats") or "",
            }
            for r in ranked
        ]
    )
    view["Projected"] = ["" if v is None else str(v) for v in view["Projected"].tolist()]
    assert view["Projected"].iloc[0] == "4 R · 2 HR · 5 RBI · 1 SB · .310 AVG"


# ---------------------------------------------------------------------------
# #228: coverage-aware auto-drop (interim retention bridge to #227)
# ---------------------------------------------------------------------------


def _ros_roster_228():
    """OF-heavy roster: two starters, a Protect bench bat, a Stream bench bat."""
    return [
        hitter(201, ["OF"], 20.0, ros_value=12.0, r=4.0, hits=6.0, ab=20.0),
        hitter(202, ["OF"], 18.0, ros_value=10.0, r=3.0, hits=5.0, ab=20.0),
        hitter(203, ["OF"], 1.0, ros_value=15.0, r=1.0, hits=2.0, ab=10.0),
        hitter(204, ["OF"], 5.0, ros_value=1.5, r=2.0, hits=3.0, ab=12.0),
    ]


def test_auto_drop_prefers_stream_over_lowest_dollar_protect():
    """Lowest-$ bench bat is Protect (high ROS) — the Stream bat is picked."""
    roster = _ros_roster_228()
    add = hitter(205, ["OF"], 30.0, ros_value=2.0, r=5.0, hits=7.0, ab=20.0)

    key, msg, info = suggest_drop(roster, {"OF": 2}, add)
    assert key is not None
    # Old policy picked H203 ($1.0). New policy must not.
    assert key[0] == 204
    assert info["retention"] == RETAIN_STREAM
    assert "stream pool" in msg
    assert "OF covered" in msg
    assert "$5.0" in msg

    result = analyze_add_drop(
        roster, {"OF": 2}, add=add, auto_suggest_drop=True, plan_rows=FULL_PLAN
    )
    assert result.ok
    assert result.drop_nfbc_id == 204
    assert result.drop_suggested
    assert result.drop_retention_label == RETAIN_STREAM
    assert result.drop_coverage_note == "OF covered"


def test_auto_drop_skips_sole_c_backup_for_covered_stream():
    """Sole C backup (thin cover) loses to a fully covered OF stream bat."""
    roster = [
        hitter(211, ["C"], 20.0, ros_value=10.0),
        hitter(212, ["C"], 18.0, ros_value=9.0),
        hitter(213, ["C"], 1.0, ros_value=1.0),
        hitter(214, ["OF"], 15.0, ros_value=5.0),
        hitter(215, ["OF"], 4.0, ros_value=1.0),
    ]
    add = hitter(216, ["OF"], 25.0, ros_value=2.0)

    key, msg, info = suggest_drop(roster, {"C": 2, "OF": 1}, add)
    assert key is not None
    assert key[0] == 215  # not the $1.0 sole C backup (H213)
    assert "OF covered" in msg

    result = analyze_add_drop(
        roster,
        {"C": 2, "OF": 1},
        add=add,
        auto_suggest_drop=True,
        plan_rows=FULL_PLAN,
    )
    assert result.ok
    assert result.drop_nfbc_id == 215


def test_auto_drop_blocked_when_every_option_holes_coverage():
    """Single-C roster + non-C add: auto-drop is blocked, explicit works."""
    roster = [hitter(221, ["C"], 5.0, ros_value=2.0)]
    add = hitter(222, ["OF"], 30.0, ros_value=2.0)

    key, msg, info = suggest_drop(roster, {"C": 1}, add)
    assert key is None
    assert info is None
    assert "coverage hole" in msg.lower()

    blocked = analyze_add_drop(
        roster, {"C": 1}, add=add, auto_suggest_drop=True, plan_rows=FULL_PLAN
    )
    assert not blocked.ok
    assert "coverage hole" in blocked.message.lower()

    # Explicit drop override still works.
    explicit = analyze_add_drop(
        roster,
        {"C": 1},
        add=add,
        drop_key=(221, "hitter"),
        auto_suggest_drop=True,
        plan_rows=FULL_PLAN,
    )
    assert explicit.ok
    assert explicit.drop_nfbc_id == 221
    assert not explicit.drop_suggested


def test_auto_drop_standalone_fallback_without_plan_rows():
    """No overall-mobility plan (stand-alone league) still picks the stream."""
    roster = _ros_roster_228()
    add = hitter(205, ["OF"], 30.0, ros_value=2.0, r=5.0, hits=7.0, ab=20.0)

    result = analyze_add_drop(
        roster, {"OF": 2}, add=add, auto_suggest_drop=True, plan_rows=None
    )
    assert result.ok
    assert result.drop_nfbc_id == 204
    assert result.drop_retention_label == RETAIN_STREAM
    assert "stream pool" in result.message


def test_retention_label_override_flips_pick_for_mart_swap():
    """Explicit #227-style labels bypass the interim ROS heuristic."""
    roster = _ros_roster_228()
    add = hitter(205, ["OF"], 30.0, ros_value=2.0)

    key, _msg, info = suggest_drop(
        roster,
        {"OF": 2},
        add,
        retention_labels={(204, "hitter"): "protect", 203: "stream"},
    )
    assert key is not None
    assert key[0] == 203
    assert info["retention"] == RETAIN_STREAM


def test_interim_retention_bands_degrade_unknown_without_ros():
    assert classify_interim_retention(hitter(231, ["OF"], 1.0), is_bench=True) == (
        "unknown"
    )
    assert (
        classify_interim_retention(
            hitter(232, ["OF"], 1.0, ros_value=15.0), is_bench=True
        )
        == RETAIN_PROTECT
    )
    assert (
        classify_interim_retention(
            hitter(233, ["OF"], 5.0, ros_value=1.5), is_bench=True
        )
        == RETAIN_STREAM
    )
