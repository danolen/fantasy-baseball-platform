"""Team-fit lineup weights helper (#218)."""

from __future__ import annotations

import pytest

from lineup_optimizer import optimize_week
from lineup_weights import (
    ratio_context_from_plan_rows,
    team_fit_inputs_ready,
    team_fit_optimize_kwargs,
    weights_from_plan_rows,
)


def test_weights_from_plan_rows_maps_categories():
    rows = [
        {"category": "R", "overall_points_per_raw_unit": 2.5},
        {"category": "SB", "overall_points_per_raw_unit": 18.0},
        {"category": "AVG", "overall_points_per_raw_unit": 22.5},
        {"category": "ERA", "overall_points_per_raw_unit": -13.5},  # abs
        {"category": "HR", "overall_points_per_raw_unit": None},  # skip
    ]
    weights = weights_from_plan_rows(rows)
    assert weights == {
        "r": 2.5,
        "sb": 18.0,
        "avg": 22.5,
        "era": 13.5,
    }


def test_ratio_context_from_plan_volumes():
    rows = [
        {"category": "AVG", "volume_h": 1406.0, "volume_ab": 5326.0},
        {"category": "ERA", "volume_er": 380.0, "volume_ip": 993.0},
        {"category": "WHIP", "volume_bb_h": 1140.0, "volume_ip": 993.0},
        {"category": "R", "volume_ab": 999.0},  # ignored for context
    ]
    ctx = ratio_context_from_plan_rows(rows)
    assert ctx == {
        "hits": 1406.0,
        "at_bats": 5326.0,
        "earned_runs": 380.0,
        "innings_pitched": 993.0,
        "walks_hits_allowed": 1140.0,
    }


def test_team_fit_ready_requires_ratio_volume_when_weighted():
    ok, _ = team_fit_inputs_ready({"r": 1.0, "sb": 2.0}, {})
    assert ok
    ok, msg = team_fit_inputs_ready({"avg": 20.0}, {})
    assert not ok
    assert "H/AB" in msg
    ok, _ = team_fit_inputs_ready(
        {"avg": 20.0}, {"hits": 100.0, "at_bats": 400.0}
    )
    assert ok


def test_team_fit_weights_flip_sit_start_vs_neutral_dollars():
    """Mirror #60 SB-vs-HR fixture using plan-derived weights."""
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

    neutral = optimize_week(players, slots, mode="monday")
    assert neutral.starter_ids() == {1}

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
    weights = weights_from_plan_rows(plan_rows)
    ctx = ratio_context_from_plan_rows(plan_rows)
    ready, msg = team_fit_inputs_ready(weights, ctx)
    assert ready, msg

    team_fit = optimize_week(
        players, slots, mode="monday", weights=weights, ratio_context=ctx
    )
    assert team_fit.starter_ids() == {2}
    assert team_fit.starter_ids() != neutral.starter_ids()


def test_team_fit_optimize_kwargs_ready_and_fallback():
    ready_rows = [
        {"category": "SB", "overall_points_per_raw_unit": 18.0},
        {
            "category": "AVG",
            "overall_points_per_raw_unit": 22.5,
            "volume_h": 1406.0,
            "volume_ab": 5326.0,
        },
    ]
    kwargs, ready, msg = team_fit_optimize_kwargs(ready_rows)
    assert ready
    assert msg == ""
    assert kwargs["weights"]["sb"] == 18.0
    assert kwargs["ratio_context"]["hits"] == 1406.0

    missing_avg_volume = [
        {"category": "AVG", "overall_points_per_raw_unit": 22.5},
    ]
    kwargs, ready, msg = team_fit_optimize_kwargs(missing_avg_volume)
    assert not ready
    assert kwargs == {}
    assert "H/AB" in msg
