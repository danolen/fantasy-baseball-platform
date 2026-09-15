"""Projected-finish helpers for Overall Standings (#221)."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from projected_finish import (
    assumptions_lines,
    category_detail_frame,
    empty_fallback_message,
    filter_team_rows,
    ordered_scenarios,
    points_metric_value,
    rank_metric_value,
    scenario_label,
    scenario_row,
    snapshot_is_stale,
    streaming_applicable,
)


def _parent_row(**overrides):
    row = {
        "league": "nolen_oc",
        "team": "Nolen OC",
        "owner": "nolen",
        "scenario": "stable",
        "streaming_applicable": True,
        "assumption_set": "ros_core_assumptions_v1",
        "week_of": date(2026, 9, 7),
        "snapshot_date": date(2026, 9, 14),
        "weeks_elapsed": 24,
        "weeks_remaining": 3,
        "current_weight": 0.89,
        "n_stream_hitters": 0,
        "n_stream_pitchers": 1,
        "n_core_starters_hitters": 14,
        "n_core_starters_pitchers": 8,
        "replacement_window": "5-10",
        "projected_overall_rank": 42,
        "projected_rank_low": 38,
        "projected_rank_high": 47,
        "projected_overall_points": 5100.5,
        "output_kind": "projected overall finish",
    }
    row.update(overrides)
    return row


def test_filter_team_rows_matches_team_then_owner():
    df = pd.DataFrame(
        [
            _parent_row(team="Nolen OC", owner="nolen", scenario="stable"),
            _parent_row(team="Nolen OC", owner="nolen", scenario="balanced"),
            _parent_row(team="Other", owner="someone", scenario="stable"),
        ]
    )
    hit = filter_team_rows(df, "Nolen OC")
    assert sorted(hit["scenario"].tolist()) == ["balanced", "stable"]

    owner_only = pd.DataFrame(
        [_parent_row(team="Satellite Label", owner="Nolen 50", scenario="stable")]
    )
    by_owner = filter_team_rows(owner_only, "Nolen 50")
    assert len(by_owner) == 1
    assert by_owner.iloc[0]["team"] == "Satellite Label"


def test_filter_team_rows_does_not_use_rank():
    df = pd.DataFrame(
        [
            _parent_row(
                team="Nolen OC",
                current_overall_rank=12,
                projected_overall_rank=12,
            )
        ]
    )
    assert filter_team_rows(df, 12).empty
    assert filter_team_rows(df, "12").empty
    assert not filter_team_rows(df, "Nolen OC").empty


def test_filter_team_rows_empty_inputs():
    assert filter_team_rows(pd.DataFrame(), "Nolen OC").empty
    df = pd.DataFrame([_parent_row()])
    assert filter_team_rows(df, None).empty
    assert filter_team_rows(df, "missing").empty


def test_snapshot_is_stale_compares_dates():
    assert snapshot_is_stale(date(2026, 9, 1), date(2026, 9, 14)) is True
    assert snapshot_is_stale(date(2026, 9, 14), date(2026, 9, 14)) is False
    assert snapshot_is_stale(date(2026, 9, 15), date(2026, 9, 14)) is False
    assert snapshot_is_stale(datetime(2026, 9, 1, 12, 0), "2026-09-14") is True
    assert snapshot_is_stale(None, date(2026, 9, 14)) is False
    assert snapshot_is_stale(date(2026, 9, 1), None) is False


def test_empty_fallback_messages():
    err = empty_fallback_message(load_error=RuntimeError("TABLE_NOT_FOUND"))
    assert "mart_projected_overall_finish" in err
    assert "TABLE_NOT_FOUND" in err
    assert "unchanged" in err

    unmatched = empty_fallback_message(
        no_team_match=True, selected_team="Nolen OC"
    )
    assert "Nolen OC" in unmatched
    assert "not current rank" in unmatched

    empty = empty_fallback_message(no_rows=True)
    assert "**not** official standings" in empty


def test_ordered_scenarios_stable_first():
    df = pd.DataFrame(
        [
            {"scenario": "aggressive"},
            {"scenario": "stable"},
            {"scenario": "balanced"},
        ]
    )
    assert ordered_scenarios(df) == ["stable", "balanced", "aggressive"]
    assert scenario_label("balanced") == "Balanced streaming"


def test_streaming_applicable_coerces_athena_bools():
    assert streaming_applicable(
        pd.DataFrame([{"streaming_applicable": False}])
    ) is False
    assert streaming_applicable(
        pd.DataFrame([{"streaming_applicable": "false"}])
    ) is False
    assert streaming_applicable(
        pd.DataFrame([{"streaming_applicable": True}])
    ) is True


def test_assumptions_lines_include_snapshot_and_50s_frozen_note():
    lines = assumptions_lines(_parent_row())
    joined = " ".join(lines)
    assert "ros_core_assumptions_v1" in joined
    assert "2026-09-14" in joined
    assert "24/27" in joined
    assert "projected overall finish" in joined
    assert "Draft-and-hold" not in joined

    frozen = assumptions_lines(
        _parent_row(streaming_applicable=False, format="50s")
    )
    assert any("streaming is not applicable" in line for line in frozen)


def test_assumptions_lines_tolerate_nan_weeks():
    lines = assumptions_lines(
        _parent_row(weeks_elapsed=float("nan"), weeks_remaining=None)
    )
    joined = " ".join(lines)
    assert "Season —/—" in joined


def test_rank_and_points_metrics():
    row = _parent_row()
    assert rank_metric_value(row) == "42 (38–47)"
    assert points_metric_value(row) == "5100.5"
    tied = _parent_row(projected_rank_low=42, projected_rank_high=42)
    assert rank_metric_value(tied) == "42"
    assert rank_metric_value(None) == "—"
    assert points_metric_value({"projected_overall_points": float("nan")}) == "—"


def test_category_detail_frame_orders_and_hides_ratio_remain():
    cat = pd.DataFrame(
        [
            {
                "team": "Nolen OC",
                "scenario": "balanced",
                "category": "AVG",
                "is_ratio": True,
                "current_raw": 0.271,
                "remaining_core_raw": None,
                "remaining_replacement_raw": None,
                "projected_final": 0.269,
                "projected_category_points": 812.5,
                "cutline_method": "blend_current_and_2025_rate_no_annualize",
            },
            {
                "team": "Nolen OC",
                "scenario": "balanced",
                "category": "HR",
                "is_ratio": False,
                "current_raw": 180.0,
                "remaining_core_raw": 12.0,
                "remaining_replacement_raw": 3.0,
                "projected_final": 195.0,
                "projected_category_points": 900.0,
                "cutline_method": "blend_annualized_current_and_2025_percentile",
            },
            {
                "team": "Nolen OC",
                "scenario": "stable",
                "category": "HR",
                "is_ratio": False,
                "current_raw": 180.0,
                "remaining_core_raw": 14.0,
                "remaining_replacement_raw": 0.0,
                "projected_final": 194.0,
                "projected_category_points": 880.0,
                "cutline_method": "blend_annualized_current_and_2025_percentile",
            },
        ]
    )
    out = category_detail_frame(cat, "balanced")
    assert list(out["Category"]) == ["HR", "AVG"]
    hr = out.iloc[0]
    assert hr["Remain core"] == "12.0"
    assert hr["Remain repl."] == "3.0"
    assert hr["Projected final"] == "195.0"
    avg = out.iloc[1]
    assert avg["Remain core"] == "—"
    assert avg["Remain repl."] == "—"
    assert avg["Current"] == "0.271"
    assert category_detail_frame(cat, "aggressive").empty


def test_scenario_row_picks_named_assumption():
    df = pd.DataFrame(
        [
            _parent_row(scenario="stable", projected_overall_rank=40),
            _parent_row(scenario="aggressive", projected_overall_rank=55),
        ]
    )
    row = scenario_row(df, "aggressive")
    assert row is not None
    assert int(row["projected_overall_rank"]) == 55
