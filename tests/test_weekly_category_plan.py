"""Weekly category plan noise-floor and target math (#186)."""

from __future__ import annotations

import pytest

from lineup_optimizer import optimize_week
from lineup_weights import team_fit_optimize_kwargs
from weekly_category_plan import (
    build_category_plan_rows,
    classify_gap,
    gap_is_meaningful,
    noise_floor_raw,
    projection_for_category,
    stretch_target_column,
)


def test_stretch_ladder_rejects_one_and_five():
    with pytest.raises(ValueError):
        stretch_target_column(1)
    with pytest.raises(ValueError):
        stretch_target_column(5)
    assert stretch_target_column(25) == "stretch_weekly_target_25"


def test_noise_floor_at_least_one_raw_unit():
    # Exact integer island (span 0) still needs a 1-SB decision unit.
    assert noise_floor_raw(tie_cluster_raw_width=0.0, raw_unit_size=1.0) == 1.0
    assert noise_floor_raw(tie_cluster_raw_width=0.5, raw_unit_size=1.0) == 1.0
    assert noise_floor_raw(tie_cluster_raw_width=2.0, raw_unit_size=1.0) == 2.0


def test_sb_tie_cluster_142_is_not_a_meaningful_ranking():
    """Issue #186 worked example (2026-07-31).

    Seventeen teams sat on exactly 142 SB. Two lineups projected 142.13 vs
    142.00 — an 0.13 raw gap that looked like ~8.5 overall points on
    point-estimate cutline math but is inside the noise floor of one SB.
    """
    floor = noise_floor_raw(tie_cluster_raw_width=0.0, raw_unit_size=1.0)
    gap = 142.13 - 142.00
    assert gap < floor
    assert gap_is_meaningful(gap, floor) is False

    result = classify_gap(
        142.13, 142.00, higher_is_better=True, noise_floor=floor
    )
    assert result["label"] == "no meaningful difference"
    assert result["meaningful"] is False


def test_counting_hand_calc_maintain_and_stretch():
    # 180 R through 18 weeks → maintain 10 R/week.
    # +25 pts needs 5 more R over 8 remaining weeks → +0.625/week.
    weeks_elapsed, weeks_remaining = 18, 8
    current_raw = 180.0
    raw_gap_up_25 = 5.0
    maintain = current_raw / weeks_elapsed
    stretch = maintain + raw_gap_up_25 / weeks_remaining
    assert maintain == pytest.approx(10.0)
    assert stretch == pytest.approx(10.625)

    floor = 1.0
    assert classify_gap(10.2, maintain, higher_is_better=True, noise_floor=floor)[
        "label"
    ] == "no meaningful difference"
    assert classify_gap(12.0, maintain, higher_is_better=True, noise_floor=floor)[
        "label"
    ] == "ahead"


def test_ratio_hand_calcs_avg_era_whip():
    totals = {
        "hits": 12.0,
        "ab": 40.0,
        "avg": 0.300,
        "er": 4.0,
        "ip": 9.0,
        "era": 4.0,
        "hits_allowed": 8.0,
        "walks_allowed": 1.0,
        "whip": 1.0,
    }
    assert projection_for_category("AVG", totals) == pytest.approx(0.300)
    assert projection_for_category("ERA", totals) == pytest.approx(4.0)
    assert projection_for_category("WHIP", totals) == pytest.approx(1.0)

    avg_floor = noise_floor_raw(tie_cluster_raw_width=0.0, raw_unit_size=0.001)
    assert classify_gap(0.300, 0.290, higher_is_better=True, noise_floor=avg_floor)[
        "label"
    ] == "ahead"

    era_floor = noise_floor_raw(tie_cluster_raw_width=0.0, raw_unit_size=0.01)
    assert classify_gap(3.50, 4.00, higher_is_better=False, noise_floor=era_floor)[
        "label"
    ] == "ahead"
    whip_floor = noise_floor_raw(tie_cluster_raw_width=0.0, raw_unit_size=0.005)
    assert classify_gap(1.05, 1.20, higher_is_better=False, noise_floor=whip_floor)[
        "label"
    ] == "ahead"


def test_build_category_plan_rows_wires_projection_and_recommendation():
    plan_rows = [
        {
            "category": "SB",
            "higher_is_better": True,
            "is_ratio": False,
            "current_raw": 142.0,
            "current_category_points": 2000.0,
            "maintain_weekly_target": 1.0,
            "stretch_weekly_target_25": 1.05,
            "noise_floor_raw": 1.0,
            "tie_cluster_raw_width": 0.0,
            "raw_unit_size": 1.0,
            "teams_at_current_points": 17,
            "headroom_status": "open",
            "ladder_up_status_25": "ok",
        }
    ]
    # 0.13 SB week vs ~1.0 targets → gaps inside the 1-SB noise floor.
    totals = {"sb": 0.13, "r": 0, "hr": 0, "rbi": 0, "k": 0, "w": 0, "sv": 0}
    rows = build_category_plan_rows(plan_rows, totals, stretch_points=25)
    assert len(rows) == 1
    assert rows[0]["projection"] == pytest.approx(0.13)
    assert rows[0]["maintain_label"] == "no meaningful difference"
    assert rows[0]["stretch_label"] == "no meaningful difference"
    assert rows[0]["recommendation"] == "no meaningful difference"


def test_weekly_plan_projection_uses_team_fit_starters_not_neutral_dollars():
    """Overall Standings Projected column follows the team-fit Monday lineup."""
    slugger = {
        "nfbc_id": 1,
        "player_name": "Slugger",
        "row_type": "hitter",
        "pos_raw": "2B",
        "pos_array": ["2B"],
        "dollars": 20.0,
        "dollars_monday_thursday": 20.0,
        "r": 4.0,
        "hr": 2.0,
        "rbi": 5.0,
        "sb": 0.0,
        "hits": 6.0,
        "ab": 24.0,
    }
    speedster = {
        "nfbc_id": 2,
        "player_name": "Speedster",
        "row_type": "hitter",
        "pos_raw": "2B",
        "pos_array": ["2B"],
        "dollars": 12.0,
        "dollars_monday_thursday": 12.0,
        "r": 3.0,
        "hr": 0.0,
        "rbi": 2.0,
        "sb": 2.0,
        "hits": 6.0,
        "ab": 24.0,
    }
    players = [slugger, speedster]
    slots = {"2B": 1}
    plan_rows = [
        {"category": "R", "overall_points_per_raw_unit": 2.5},
        {"category": "HR", "overall_points_per_raw_unit": 1.0},
        {"category": "RBI", "overall_points_per_raw_unit": 0.5},
        {"category": "SB", "overall_points_per_raw_unit": 18.0},
        {
            "category": "AVG",
            "overall_points_per_raw_unit": 22.5,
            "volume_h": 1406.0,
            "volume_ab": 5326.0,
        },
    ]

    neutral = optimize_week(players, slots, mode="monday")
    opt_kwargs, ready, msg = team_fit_optimize_kwargs(plan_rows)
    assert ready, msg
    team_fit = optimize_week(players, slots, mode="monday", **opt_kwargs)

    ui_plan = [
        {
            "category": "SB",
            "higher_is_better": True,
            "is_ratio": False,
            "current_raw": 142.0,
            "current_category_points": 2000.0,
            "maintain_weekly_target": 1.0,
            "stretch_weekly_target_25": 1.05,
            "noise_floor_raw": 1.0,
            "teams_at_current_points": 17,
        }
    ]
    neutral_rows = build_category_plan_rows(
        ui_plan, neutral.totals, stretch_points=25
    )
    team_fit_rows = build_category_plan_rows(
        ui_plan, team_fit.totals, stretch_points=25
    )
    assert neutral_rows[0]["projection"] == pytest.approx(0.0)
    assert team_fit_rows[0]["projection"] == pytest.approx(2.0)
