"""Old-vs-new ROS SGP comparison for #184 (read-only Athena SELECTs).

1. Runs the new mart_ros_sgp_calibration SQL against prod tables.
2. Prints old (2025 file + hardcoded constants) vs new (blended) slopes
   and ratio contexts side by side.
3. Runs the new OC hitting/pitching SGP model SQL with the calibration
   inlined, and compares per-player SGP + rank vs the current prod tables.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from validate_ros_sgp_calibration import run_athena

STAGE = "dbt_stage"
MAIN = "dbt_main"

CALIB_REFS = {
    "{{ ref('season_scoring_calendar') }}": "(SELECT 2026 AS season_year, 27 AS scoring_periods, DATE '2026-03-27' AS season_start_date)",
    "{{ ref('mart_sgp_factors') }}": f"{MAIN}.mart_sgp_factors",
    "{{ ref('stg_nfbc_in_season_overall_category_stats') }}": f"{STAGE}.stg_nfbc_in_season_overall_category_stats",
}


def load_model(path):
    with open(path) as f:
        sql = f.read()
    return sql[sql.index("}}") + 2:]


def swap_refs(sql, mapping):
    for k, v in mapping.items():
        sql = sql.replace(k, v)
    assert "{{" not in sql, "unrendered Jinja remains"
    return sql


def calib_cte():
    return "calib AS (" + swap_refs(load_model("dbt/models/main/mart_ros_sgp_calibration.sql"), CALIB_REFS) + ")"


def new_sgp_query(model_path, extra_refs):
    mapping = dict(extra_refs)
    mapping["{{ ref('mart_ros_sgp_calibration') }}"] = "calib"
    body = swap_refs(load_model(model_path), mapping)
    m = re.search(r"(?im)^\s*with\s", body)
    assert m, "no WITH anchor in SGP model"
    return body[:m.start()] + "WITH " + calib_cte() + ",\n" + body[m.end():]


def q(sql):
    header, data = run_athena(sql)
    return header, data


def show_calibration():
    header, data = q(swap_refs(load_model("dbt/models/main/mart_ros_sgp_calibration.sql"), CALIB_REFS)
                     + " ORDER BY 1, 2")
    idx = {c: i for i, c in enumerate(header)}
    print("== calibration: format | cat | hist -> final slope | w | completion | fallback ==")
    for r in data:
        print(f"{r[idx['format']]:>4} {r[idx['category']]:>4} "
              f"{float(r[idx['hist_slope']]):>10.4f} -> {float(r[idx['final_slope']]):>10.4f} "
              f"w={float(r[idx['current_weight']]):.3f} "
              f"compl={float(r[idx['season_completion']]):.3f} "
              f"fallback={r[idx['is_fallback']]} snap={r[idx['snapshot_date']]}")
    print()
    print("== ratio contexts: format | cat | (num, den, rate) hist -> final ==")
    for r in data:
        if r[idx['category']] in ("AVG", "ERA", "WHIP"):
            print(f"{r[idx['format']]:>4} {r[idx['category']]:>4} "
                  f"({r[idx['hist_ctx_num']]}, {r[idx['hist_ctx_den']]}, {r[idx['hist_ctx_rate']]}) -> "
                  f"({float(r[idx['final_ctx_num']]):.1f}, {float(r[idx['final_ctx_den']]):.1f}, "
                  f"{float(r[idx['final_ctx_rate']]):.4f})")


def compare_sgp(model_path, extra_refs, old_table, label):
    new_sql = f"WITH new_sgp AS ({new_sgp_query(model_path, extra_refs)}) " \
              f"SELECT id, name, pos, CAST(sgp AS DOUBLE) AS new_sgp FROM new_sgp"
    header, new_rows = q(new_sql)
    header2, old_rows = q(f"SELECT id, CAST(sgp AS DOUBLE) AS old_sgp FROM {old_table}")
    old = {r[0]: float(r[1]) for r in old_rows}
    new = {r[0]: (r[1], float(r[3])) for r in new_rows}
    common = [k for k in new if k in old]
    diffs = []
    for k in common:
        name, nv = new[k]
        ov = old[k]
        diffs.append((nv - ov, ov, nv, name, k))
    diffs.sort(key=lambda t: -abs(t[0]))
    n = len(common)
    # Rank correlation (Spearman) on common ids.
    ro = {k: i for i, k in enumerate(sorted(common, key=lambda k: -old[k]))}
    rn = {k: i for i, k in enumerate(sorted(common, key=lambda k: -new[k][1]))}
    d2 = sum((ro[k] - rn[k]) ** 2 for k in common)
    spearman = 1 - 6 * d2 / (n * (n * n - 1)) if n > 2 else float("nan")
    mad = sum(abs(d[0]) for d in diffs) / n
    print(f"== {label}: {n} common players | Spearman rank corr = {spearman:.4f} | mean|dSGP| = {mad:.3f}")
    print("   biggest movers (new-old SGP, old rank -> new rank):")
    for d, ov, nv, name, k in diffs[:12]:
        print(f"   {name} ({k}): {d:+.2f}  [{ro[k] + 1} -> {rn[k] + 1}]")
    print()


AGG_HIT = f"{STAGE}.stg_proj_rest_of_season_hitting_agg"
AGG_PIT = f"{STAGE}.stg_proj_rest_of_season_pitching_agg"
IDMAP = f"{STAGE}.stg_mpd_player_id_map"


def main():
    show_calibration()
    compare_sgp(
        "dbt/models/stage/projections/rest_of_season/hitting/stg_proj_rest_of_season_hitting_sgp_oc.sql",
        {"{{ ref('stg_proj_rest_of_season_hitting_agg') }}": AGG_HIT,
         "{{ ref('stg_mpd_player_id_map') }}": IDMAP},
        f"{STAGE}.stg_proj_rest_of_season_hitting_sgp_oc",
        "OC hitting SGP",
    )
    compare_sgp(
        "dbt/models/stage/projections/rest_of_season/pitching/stg_proj_rest_of_season_pitching_sgp_oc.sql",
        {"{{ ref('stg_proj_rest_of_season_pitching_agg') }}": AGG_PIT,
         "{{ ref('stg_mpd_player_id_map') }}": IDMAP},
        f"{STAGE}.stg_proj_rest_of_season_pitching_sgp_oc",
        "OC pitching SGP",
    )


if __name__ == "__main__":
    main()
