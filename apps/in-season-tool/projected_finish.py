"""Projected overall-finish helpers for the Overall Standings tab (#221).

Pure dataframe/label logic — Athena loaders and Streamlit widgets stay in
``app.py``. Identity is team name / owner from the Overall Standings selector,
never current rank.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from weekly_category_plan import CATEGORY_ORDER

SCENARIO_ORDER = ("stable", "balanced", "aggressive")

SCENARIO_LABELS = {
    "stable": "Stable roster",
    "balanced": "Balanced streaming",
    "aggressive": "Aggressive streaming",
}

OUTPUT_KIND = "projected overall finish"

FAAB_WORKSHEET_NOTE = (
    "FAAB add/drop impact stays on the **FAAB Worksheet** tab (#187); this "
    "section does not re-run what-if bids."
)


def filter_team_rows(df: pd.DataFrame, selected_team: Any) -> pd.DataFrame:
    """Rows for the Overall Standings team selector (team name, then owner)."""
    if df is None or df.empty:
        return pd.DataFrame()
    if selected_team is None or str(selected_team).strip() == "":
        return df.iloc[0:0].copy()
    label = str(selected_team)
    if "team" in df.columns:
        hit = df[df["team"].astype(str) == label]
        if not hit.empty:
            return hit.copy()
    if "owner" in df.columns:
        hit = df[df["owner"].astype(str) == label]
        if not hit.empty:
            return hit.copy()
    return df.iloc[0:0].copy()


def _as_date(value: Any) -> Optional[date]:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    try:
        return pd.to_datetime(text).date()
    except (TypeError, ValueError):
        return None


def snapshot_is_stale(finish_snapshot: Any, standings_snapshot: Any) -> bool:
    """True when a finish snapshot exists and is older than current standings."""
    finish_d = _as_date(finish_snapshot)
    standings_d = _as_date(standings_snapshot)
    if finish_d is None or standings_d is None:
        return False
    return finish_d < standings_d


def ordered_scenarios(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty or "scenario" not in df.columns:
        return []
    present = {str(s) for s in df["scenario"].dropna().unique()}
    return [s for s in SCENARIO_ORDER if s in present]


def scenario_label(scenario: Any) -> str:
    key = str(scenario) if scenario is not None else ""
    return SCENARIO_LABELS.get(key, key or "—")


def _as_bool(value: Any) -> Optional[bool]:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "t", "1", "yes"):
        return True
    if text in ("false", "f", "0", "no"):
        return False
    return None


def _as_num(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and value != value):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return num


def streaming_applicable(df: pd.DataFrame) -> Optional[bool]:
    if df is None or df.empty or "streaming_applicable" not in df.columns:
        return None
    vals = df["streaming_applicable"].dropna()
    if vals.empty:
        return None
    return _as_bool(vals.iloc[0])


def empty_fallback_message(
    *,
    load_error: Optional[BaseException] = None,
    no_rows: bool = False,
    no_team_match: bool = False,
    selected_team: Any = None,
) -> str:
    """User-facing copy when the #188 mart is missing, empty, or unmatched."""
    if load_error is not None:
        err = str(load_error).rstrip(".")
        return (
            "Projected finish could not load (`mart_projected_overall_finish`). "
            f"{err}. Rebuild the #188 marts, then refresh. Current "
            "standings and mobility above are unchanged."
        )
    if no_team_match:
        return (
            f"No projected-finish row for **{selected_team}**. Identity is "
            "the Overall Standings team selector (team name / owner), not "
            "current rank. Current standings and mobility still apply."
        )
    if no_rows:
        return (
            "No projected-finish rows for this contest yet. The #188 marts "
            "may not be built, or this snapshot has no configured Nolen "
            "overall entry. This is **not** official standings and **not** "
            "the mobility grid."
        )
    return "No projected-finish data."


def first_row(df: pd.DataFrame) -> Optional[Mapping[str, Any]]:
    if df is None or df.empty:
        return None
    return df.iloc[0].to_dict()


def assumptions_lines(row: Mapping[str, Any] | None) -> list[str]:
    if not row:
        return []
    window = row.get("replacement_window") or "—"
    elapsed = _as_num(row.get("weeks_elapsed"))
    remaining = _as_num(row.get("weeks_remaining"))
    elapsed_s = "—" if elapsed is None else str(int(elapsed))
    remaining_s = "—" if remaining is None else str(int(remaining))
    if elapsed is None or remaining is None:
        total_s = "—"
    else:
        total_s = str(int(elapsed + remaining))
    weight_n = _as_num(row.get("current_weight"))
    weight_s = "—" if weight_n is None else weight_n
    lines = [
        f"Assumption set `{row.get('assumption_set') or '—'}` · week of "
        f"`{row.get('week_of') or '—'}` · finish snapshot "
        f"`{row.get('snapshot_date') or '—'}`.",
        (
            f"Season {elapsed_s}/{total_s}"
            f" scoring weeks · remaining `{remaining_s}` · "
            f"blend weight `{weight_s}`. "
            "Counting cutlines annualize; AVG/ERA/WHIP do not."
        ),
        (
            f"Stream slots H `{row.get('n_stream_hitters')}` / P "
            f"`{row.get('n_stream_pitchers')}` · replacement window `{window}` "
            f"· core starters H `{row.get('n_core_starters_hitters')}` / P "
            f"`{row.get('n_core_starters_pitchers')}`."
        ),
    ]
    if _as_bool(row.get("streaming_applicable")) is False:
        lines.append(
            "Draft-and-hold: streaming is not applicable, so stable / "
            "balanced / aggressive are the same frozen roster."
        )
    kind = row.get("output_kind") or OUTPUT_KIND
    lines.append(
        f"Output is **{kind}** against forecast contest cutlines — not "
        "projected standings for every contest team."
    )
    return lines


def _fmt_raw(val: Any, is_ratio: bool) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "—"
    return f"{num:.3f}" if is_ratio else f"{num:.1f}"


def _fmt_pts(val: Any) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    try:
        return f"{float(val):.1f}"
    except (TypeError, ValueError):
        return "—"


def category_detail_frame(
    cat_df: pd.DataFrame,
    scenario: str,
) -> pd.DataFrame:
    """One-scenario category table: current vs remaining vs projected final/pts."""
    if cat_df is None or cat_df.empty:
        return pd.DataFrame()
    subset = cat_df[cat_df["scenario"].astype(str) == str(scenario)].copy()
    if subset.empty:
        return pd.DataFrame()
    order_index = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    subset["_ord"] = subset["category"].map(lambda c: order_index.get(c, 99))
    subset = subset.sort_values(["_ord", "category"])
    rows = []
    for rec in subset.to_dict(orient="records"):
        is_ratio = bool(rec.get("is_ratio"))
        rows.append(
            {
                "Category": rec.get("category"),
                "Current": _fmt_raw(rec.get("current_raw"), is_ratio),
                "Remain core": _fmt_raw(rec.get("remaining_core_raw"), False)
                if not is_ratio
                else "—",
                "Remain repl.": _fmt_raw(
                    rec.get("remaining_replacement_raw"), False
                )
                if not is_ratio
                else "—",
                "Projected final": _fmt_raw(rec.get("projected_final"), is_ratio),
                "Projected cat pts": _fmt_pts(
                    rec.get("projected_category_points")
                ),
                "Cutline method": rec.get("cutline_method") or "—",
            }
        )
    return pd.DataFrame(rows)


def rank_metric_value(row: Mapping[str, Any] | None) -> str:
    if not row:
        return "—"
    rank = row.get("projected_overall_rank")
    low = row.get("projected_rank_low")
    high = row.get("projected_rank_high")
    try:
        rank_s = f"{int(rank)}" if rank is not None and rank == rank else "—"
    except (TypeError, ValueError):
        rank_s = "—"
    try:
        if (
            low is not None
            and high is not None
            and low == low
            and high == high
            and int(low) != int(high)
        ):
            return f"{rank_s} ({int(low)}–{int(high)})"
    except (TypeError, ValueError):
        pass
    return rank_s


def points_metric_value(row: Mapping[str, Any] | None) -> str:
    if not row:
        return "—"
    pts = row.get("projected_overall_points")
    try:
        if pts is None or (isinstance(pts, float) and pts != pts):
            return "—"
        return f"{float(pts):.1f}"
    except (TypeError, ValueError):
        return "—"


def scenario_row(df: pd.DataFrame, scenario: str) -> Optional[Mapping[str, Any]]:
    if df is None or df.empty or "scenario" not in df.columns:
        return None
    hit = df[df["scenario"].astype(str) == str(scenario)]
    return first_row(hit)


def present_scenarios(rows: Sequence[str]) -> list[str]:
    return [s for s in SCENARIO_ORDER if s in set(rows)]
