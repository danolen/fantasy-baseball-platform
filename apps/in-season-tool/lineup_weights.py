"""Build optimize_week weights / ratio_context from weekly plan (mobility) rows.

Used by the Lineup Optimizer Team-fit mode (#218) and Overall Standings
Weekly Plan projected stats. Weight keys match
``lineup_optimizer.score_player`` (lowercase category codes).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

# Plan / mobility category label -> optimizer weight key.
CATEGORY_TO_WEIGHT_KEY = {
    "R": "r",
    "HR": "hr",
    "RBI": "rbi",
    "SB": "sb",
    "AVG": "avg",
    "K": "k",
    "W": "w",
    "SV": "sv",
    "ERA": "era",
    "WHIP": "whip",
}

OBJECTIVE_NEUTRAL = "neutral"
OBJECTIVE_TEAM_FIT = "team_fit"


def _opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, float) and value != value:  # NaN
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def weights_from_plan_rows(
    plan_rows: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    """Map ``overall_points_per_raw_unit`` onto lowercase optimizer keys.

    Missing / null categories are omitted (treated as zero weight by the
    scorer). Absolute value is used so ERA/WHIP stay positive weights; the
    engine already scores rate improvements as positive via lower-is-better
    linearization.
    """
    weights: dict[str, float] = {}
    for row in plan_rows:
        cat = str(row.get("category") or "").upper()
        key = CATEGORY_TO_WEIGHT_KEY.get(cat)
        if key is None:
            continue
        pts = _opt_float(row.get("overall_points_per_raw_unit"))
        if pts is None:
            continue
        weights[key] = abs(pts)
    return weights


def ratio_context_from_plan_rows(
    plan_rows: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    """Season-to-date numerator/denominator volumes for AVG/ERA/WHIP.

    Volumes live on the matching category rows of
    ``mart_weekly_category_plan`` (from mobility).
    """
    by_cat = {
        str(r.get("category") or "").upper(): r
        for r in plan_rows
        if r.get("category") is not None
    }
    ctx: dict[str, float] = {}

    avg = by_cat.get("AVG", {})
    hits = _opt_float(avg.get("volume_h"))
    ab = _opt_float(avg.get("volume_ab"))
    if hits is not None:
        ctx["hits"] = hits
    if ab is not None:
        ctx["at_bats"] = ab

    era = by_cat.get("ERA", {})
    er = _opt_float(era.get("volume_er"))
    ip_era = _opt_float(era.get("volume_ip"))
    if er is not None:
        ctx["earned_runs"] = er
    if ip_era is not None:
        ctx["innings_pitched"] = ip_era

    whip = by_cat.get("WHIP", {})
    bb_h = _opt_float(whip.get("volume_bb_h"))
    ip_whip = _opt_float(whip.get("volume_ip"))
    if bb_h is not None:
        ctx["walks_hits_allowed"] = bb_h
    if "innings_pitched" not in ctx and ip_whip is not None:
        ctx["innings_pitched"] = ip_whip

    return ctx


def team_fit_inputs_ready(
    weights: Mapping[str, float],
    ratio_context: Mapping[str, float],
) -> tuple[bool, str]:
    """Whether Team-fit mode has enough signal to run."""
    if not weights:
        return False, "No overall_points_per_raw_unit values for this team."
    # Counting-only still useful; ratios need context or they contribute 0.
    has_ratio_weight = any(weights.get(k) for k in ("avg", "era", "whip"))
    if has_ratio_weight:
        if weights.get("avg") and (
            "hits" not in ratio_context or "at_bats" not in ratio_context
        ):
            return (
                False,
                "AVG weight is set but season H/AB volume is missing "
                "(rebuild mart_weekly_category_plan).",
            )
        if (weights.get("era") or weights.get("whip")) and (
            "innings_pitched" not in ratio_context
        ):
            return (
                False,
                "ERA/WHIP weight is set but season IP volume is missing "
                "(rebuild mart_weekly_category_plan).",
            )
    return True, ""


def team_fit_optimize_kwargs(
    plan_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], bool, str]:
    """``optimize_week`` kwargs for Team-fit, or empty when not ready.

    Returns ``(kwargs, ready, message)``. Callers pass ``**kwargs`` into
    ``optimize_week``; when ``ready`` is false they should keep Neutral ``$``
    and surface ``message``.
    """
    weights = weights_from_plan_rows(plan_rows)
    ratio_context = ratio_context_from_plan_rows(plan_rows)
    ready, msg = team_fit_inputs_ready(weights, ratio_context)
    if not ready:
        return {}, False, msg
    return (
        {"weights": weights, "ratio_context": ratio_context},
        True,
        "",
    )
