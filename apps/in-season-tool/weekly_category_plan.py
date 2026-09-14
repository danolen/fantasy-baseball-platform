"""Weekly category plan helpers (#186).

Combines mobility-derived weekly targets with expected-lineup aggregates from
``optimize_week``. Classifies gaps against the local tie-cluster noise floor
so fractional projection differences inside a point island are not presented
as ranked recommendations.
"""

from __future__ import annotations

from typing import Any, Mapping

# Stretch ladder agreed in #183 / #186 — not 1 or 5.
STRETCH_OPTIONS = (25, 50, 100)
DEFAULT_STRETCH = 25

CATEGORY_ORDER = ("R", "HR", "RBI", "SB", "AVG", "K", "W", "SV", "ERA", "WHIP")

# Map category -> key on lineup aggregate_totals / display totals.
COUNTING_PROJECTION_KEYS = {
    "R": "r",
    "HR": "hr",
    "RBI": "rbi",
    "SB": "sb",
    "K": "k",
    "W": "w",
    "SV": "sv",
}


def noise_floor_raw(
    *,
    tie_cluster_raw_width: float | None,
    raw_unit_size: float | None,
) -> float:
    """Smallest raw delta treated as a real category move."""
    width = float(tie_cluster_raw_width or 0.0)
    unit = float(raw_unit_size or 1.0)
    return max(width, unit)


def gap_is_meaningful(gap_raw: float | None, floor_raw: float | None) -> bool:
    """True when |gap| clears the local noise floor."""
    if gap_raw is None or floor_raw is None:
        return False
    return abs(float(gap_raw)) > float(floor_raw)


def overall_points_for_raw_delta(
    raw_delta: float | None,
    *,
    overall_points_per_raw_unit: float | None,
    raw_unit_size: float | None,
) -> float | None:
    """Convert a raw category delta into overall standings points.

    ``mart_overall_category_mobility`` prices a category point as
    ``raw_unit_size / raw_per_category_point``, so
    ``overall_points_per_raw_unit`` is points per *raw_unit_size* — 0.01 of
    ERA, 0.005 of WHIP, 0.001 of AVG, 1.0 for counting stats. The raw delta
    therefore has to be expressed in those units before scaling. Multiplying a
    raw delta by the column directly understates ERA by 100x, WHIP by 200x and
    AVG by 1000x; counting categories are unaffected because their unit is 1.0.

    ``lineup_optimizer`` already divides by ``RATIO_UNITS`` before applying the
    weight, so weights fed to the optimizer stay in per-``raw_unit_size`` form.
    """
    if raw_delta is None or overall_points_per_raw_unit is None:
        return None
    unit = float(raw_unit_size or 1.0)
    if unit <= 0:
        unit = 1.0
    return (float(raw_delta) / unit) * abs(float(overall_points_per_raw_unit))


def classify_gap(
    projection: float | None,
    target: float | None,
    *,
    higher_is_better: bool,
    noise_floor: float | None,
) -> dict[str, Any]:
    """Compare projection to a weekly target with noise-floor labeling.

    Returns gap in the favorable direction (positive = ahead of target for
    higher-is-better categories; for ERA/WHIP positive means under the cap).
    """
    if projection is None or target is None:
        return {
            "projection": projection,
            "target": target,
            "gap": None,
            "meaningful": False,
            "label": "missing",
        }

    if higher_is_better:
        gap = float(projection) - float(target)
    else:
        # Lower is better: under target (fewer ER) is a positive gap.
        gap = float(target) - float(projection)

    meaningful = gap_is_meaningful(gap, noise_floor)
    if not meaningful:
        label = "no meaningful difference"
    elif gap > 0:
        label = "ahead"
    else:
        label = "behind"
    return {
        "projection": float(projection),
        "target": float(target),
        "gap": gap,
        "meaningful": meaningful,
        "label": label,
    }


def projection_for_category(
    category: str,
    totals: Mapping[str, Any],
) -> float | None:
    """Pull the expected-lineup projection for one scoring category."""
    cat = category.upper()
    if cat in COUNTING_PROJECTION_KEYS:
        val = totals.get(COUNTING_PROJECTION_KEYS[cat])
        return float(val) if val is not None else None
    if cat == "AVG":
        return float(totals["avg"]) if totals.get("avg") is not None else None
    if cat == "ERA":
        return float(totals["era"]) if totals.get("era") is not None else None
    if cat == "WHIP":
        return float(totals["whip"]) if totals.get("whip") is not None else None
    return None


def ratio_count_projection(
    category: str,
    totals: Mapping[str, Any],
) -> tuple[float | None, float | None]:
    """Numerator and denominator volume for ratio categories."""
    cat = category.upper()
    if cat == "AVG":
        return (
            float(totals["hits"]) if totals.get("hits") is not None else None,
            float(totals["ab"]) if totals.get("ab") is not None else None,
        )
    if cat == "ERA":
        return (
            float(totals["er"]) if totals.get("er") is not None else None,
            float(totals["ip"]) if totals.get("ip") is not None else None,
        )
    if cat == "WHIP":
        h = totals.get("hits_allowed")
        bb = totals.get("walks_allowed")
        ip = totals.get("ip")
        if h is None or bb is None:
            num = None
        else:
            num = float(h) + float(bb)
        den = float(ip) if ip is not None else None
        return num, den
    return None, None


def stretch_target_column(stretch_points: int) -> str:
    if stretch_points not in STRETCH_OPTIONS:
        raise ValueError(
            f"stretch_points must be one of {STRETCH_OPTIONS}, got {stretch_points}"
        )
    return f"stretch_weekly_target_{stretch_points}"


def build_category_plan_rows(
    plan_df_rows: list[Mapping[str, Any]],
    totals: Mapping[str, Any],
    *,
    stretch_points: int = DEFAULT_STRETCH,
) -> list[dict[str, Any]]:
    """Merge mart target rows with expected-lineup totals into UI rows."""
    stretch_col = stretch_target_column(stretch_points)
    rows: list[dict[str, Any]] = []
    by_cat = {str(r["category"]).upper(): r for r in plan_df_rows}

    for cat in CATEGORY_ORDER:
        raw = by_cat.get(cat)
        if raw is None:
            continue
        higher = bool(raw.get("higher_is_better", True))
        is_ratio = bool(raw.get("is_ratio", False))
        floor = raw.get("noise_floor_raw")
        if floor is None:
            floor = noise_floor_raw(
                tie_cluster_raw_width=raw.get("tie_cluster_raw_width"),
                raw_unit_size=raw.get("raw_unit_size"),
            )

        projection = projection_for_category(cat, totals)
        maintain = raw.get("maintain_weekly_target")
        stretch = raw.get(stretch_col)

        maintain_cmp = classify_gap(
            projection, maintain, higher_is_better=higher, noise_floor=floor
        )
        stretch_cmp = classify_gap(
            projection, stretch, higher_is_better=higher, noise_floor=floor
        )

        count_num, count_den = ratio_count_projection(cat, totals)

        rows.append(
            {
                "category": cat,
                "is_ratio": is_ratio,
                "higher_is_better": higher,
                "current_raw": raw.get("current_raw"),
                "current_category_points": raw.get("current_category_points"),
                "maintain_weekly_target": maintain,
                "stretch_weekly_target": stretch,
                "stretch_points": stretch_points,
                "projection": projection,
                "maintain_gap": maintain_cmp["gap"],
                "maintain_label": maintain_cmp["label"],
                "stretch_gap": stretch_cmp["gap"],
                "stretch_label": stretch_cmp["label"],
                "noise_floor_raw": floor,
                "teams_at_current_points": raw.get("teams_at_current_points"),
                "headroom_status": raw.get("headroom_status"),
                "ladder_up_status": raw.get(f"ladder_up_status_{stretch_points}"),
                "projected_numerator": count_num,
                "projected_denominator": count_den,
                "recommendation": (
                    stretch_cmp["label"]
                    if stretch_cmp["meaningful"]
                    else "no meaningful difference"
                ),
            }
        )
    return rows
