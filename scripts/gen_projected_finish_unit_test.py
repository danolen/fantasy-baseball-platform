"""Emit the #188 dbt unit-test YAML (hand-calc OC scenario)."""

CATS = [
    ("R", True, False),
    ("HR", True, False),
    ("RBI", True, False),
    ("SB", True, False),
    ("AVG", True, True),
    ("K", True, False),
    ("W", True, False),
    ("SV", True, False),
    ("ERA", False, True),
    ("WHIP", False, True),
]


def overall_row(team, owner, team_key, league_id, rank, overall_pts, r, cat, hib, is_ratio):
    if cat == "R":
        raw = r
        pts = 2
    elif cat == "AVG":
        raw = 0.25
        pts = 2
    elif cat == "ERA":
        raw = 3.0
        pts = 2
    elif cat == "WHIP":
        raw = 1.0
        pts = 2
    else:
        raw = 0.0
        pts = 2
    return (
        f"select 'nolen_oc' as contest_key, 'nolen_oc' as source_league_key, "
        f"'oc' as format, 890 as nfbc_overall_game_type_id, "
        f"date '2026-04-10' as snapshot_date, true as is_latest_snapshot, "
        f"'{team_key}' as team_key, '{owner}' as owner, '{team}' as team, "
        f"{league_id} as nfbc_league_id, {rank} as overall_rank, "
        f"cast({overall_pts} as double) as overall_points, "
        f"'{cat}' as category, cast({raw} as double) as raw_stat, "
        f"cast({pts} as double) as category_points, "
        f"{str(hib).lower()} as higher_is_better, {str(is_ratio).lower()} as is_ratio, "
        f"cast(50.0 as double) as volume_h, cast(200.0 as double) as volume_ab, "
        f"cast(3.0 as double) as volume_er, cast(9.0 as double) as volume_ip, "
        f"cast(9.0 as double) as volume_bb_h"
    )


def overall_sql():
    teams = [
        ("Nolen Squad", "Dan Nolen", "nolen_oc|1828|Nolen Squad", 1828, 2, 20, 100),
        ("Alpha", "A", "nolen_oc|1|Alpha", 1, 1, 25, 110),
        ("Beta", "B", "nolen_oc|2|Beta", 2, 3, 15, 50),
    ]
    parts = []
    for team, owner, key, lid, rank, opts, r in teams:
        for cat, hib, is_ratio in CATS:
            parts.append(overall_row(team, owner, key, lid, rank, opts, r, cat, hib, is_ratio))
    return "\n          union all ".join(parts)


# Hand-calc (periods=3, snapshot 2026-04-10, start 2026-03-27):
# weeks_elapsed=3, remaining=1, w=1.0
# 2 hitter slots, 1 pitcher slot
# two core hitters ROS R 20+8; weekly FA R=3
# stable 0H: core R=28 repl=0 final=128 1st (vs 110, 50) -> 3 pts; overall 3+18=21 rank 1
# balanced 1H: core=20 repl=3 final=123 1st -> 3 pts; overall 21 rank 1
# aggressive 2H: core=0 repl=6 final=106 2nd -> 2 pts; overall 20 rank 2

EXPECT = []
for scenario, core_r, repl_r, final_r, r_pts, n_core_h, n_repl_h, ov_pts, ov_rank in [
    ("stable", 28.0, 0.0, 128.0, 3.0, 2, 0, 21.0, 1),
    ("balanced", 20.0, 3.0, 123.0, 3.0, 1, 1, 21.0, 1),
    ("aggressive", 0.0, 6.0, 106.0, 2.0, 0, 2, 20.0, 2),
]:
    for cat, hib, is_ratio in CATS:
        if cat == "R":
            row = {
                "scenario": scenario,
                "category": cat,
                "remaining_core_raw": core_r,
                "remaining_replacement_raw": repl_r,
                "projected_final": final_r,
                "projected_category_points": r_pts,
                "n_core_starters_hitters": n_core_h,
                "n_repl_hitters": n_repl_h,
                "projected_overall_points": ov_pts,
                "projected_overall_rank": ov_rank,
                "output_kind": "projected overall finish",
            }
        elif cat == "AVG":
            core_h = {2: 14.0, 1: 10.0, 0: 0.0}[n_core_h]
            core_ab = {2: 56.0, 1: 40.0, 0: 0.0}[n_core_h]
            repl_h = float(n_repl_h) * 1.0 * 1.0
            repl_ab = float(n_repl_h) * 4.0 * 1.0
            final = (50.0 + core_h + repl_h) / (200.0 + core_ab + repl_ab)
            row = {
                "scenario": scenario,
                "category": cat,
                "remaining_core_raw": None,
                "remaining_replacement_raw": None,
                "projected_final": final,
                "projected_category_points": 2.0,
                "n_core_starters_hitters": n_core_h,
                "n_repl_hitters": n_repl_h,
                "projected_overall_points": ov_pts,
                "projected_overall_rank": ov_rank,
                "output_kind": "projected overall finish",
            }
        elif cat == "ERA":
            row = {
                "scenario": scenario,
                "category": cat,
                "remaining_core_raw": None,
                "remaining_replacement_raw": None,
                "projected_final": 3.0,
                "projected_category_points": 2.0,
                "n_core_starters_hitters": n_core_h,
                "n_repl_hitters": n_repl_h,
                "projected_overall_points": ov_pts,
                "projected_overall_rank": ov_rank,
                "output_kind": "projected overall finish",
            }
        elif cat == "WHIP":
            row = {
                "scenario": scenario,
                "category": cat,
                "remaining_core_raw": None,
                "remaining_replacement_raw": None,
                "projected_final": 1.0,
                "projected_category_points": 2.0,
                "n_core_starters_hitters": n_core_h,
                "n_repl_hitters": n_repl_h,
                "projected_overall_points": ov_pts,
                "projected_overall_rank": ov_rank,
                "output_kind": "projected overall finish",
            }
        else:
            row = {
                "scenario": scenario,
                "category": cat,
                "remaining_core_raw": 0.0,
                "remaining_replacement_raw": 0.0,
                "projected_final": 0.0,
                "projected_category_points": 2.0,
                "n_core_starters_hitters": n_core_h,
                "n_repl_hitters": n_repl_h,
                "projected_overall_points": ov_pts,
                "projected_overall_rank": ov_rank,
                "output_kind": "projected overall finish",
            }
        EXPECT.append(row)


def fmt_row(d):
    parts = []
    for k, v in d.items():
        if v is None:
            parts.append(f"{k}: null")
        elif isinstance(v, str):
            parts.append(f"{k}: '{v}'")
        elif isinstance(v, bool):
            parts.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, float):
            parts.append(f"{k}: {v}")
        else:
            parts.append(f"{k}: {v}")
    return "        - {" + ", ".join(parts) + "}"


HEADER = '''version: 2

# Projected overall-finish hand-calc (#188).
# Calendar: 3 scoring periods, snapshot 2026-04-10, start 2026-03-27
# -> weeks_elapsed=3, weeks_remaining=1, w=1.0 (hist does not move the field).
# Slots: 2 hitters, 1 pitcher. Two core hitters ROS R=20 and R=8, H/AB 10/40 and 4/16.
# FA window 1-1 weekly R=3, H=1, AB=4.
# stable 0H: core R=28 repl=0 final=128 vs field 110/50 -> 1st, 3 R-pts, overall 21 rank 1
# balanced 1H: core=20 repl=3 final=123 -> 1st, overall 21 rank 1
# aggressive 2H: core=0 repl=6 final=106 vs 110 -> 2nd, 2 R-pts, overall 20 rank 2
# AVG stays .250 from combined H/AB; ERA 3.0 / WHIP 1.0 from current volume.

unit_tests:
  - name: test_projected_finish_oc_scenarios_hand_calc
    description: >-
      OC hand-calc of core vs replacement remaining, combined AVG, and
      category/overall points against a three-team paced field.
    model: mart_projected_overall_finish_category
    given:
      - input: ref('league_config')
        format: sql
        rows: |
          select 'nolen_oc' as league, 'oc' as format, 890 as nfbc_overall_game_type_id, 1828 as nfbc_league_id
      - input: ref('season_scoring_calendar')
        format: sql
        rows: |
          select 2026 as season_year, 3 as scoring_periods, date '2026-03-27' as season_start_date
      - input: ref('league_roster_slots')
        format: sql
        rows: |
          select 'oc' as format, 'UTIL' as slot, 'hitter' as slot_group, 2 as "count"
          union all select 'oc', 'P', 'pitcher', 1
      - input: ref('ros_core_assumptions')
        format: sql
        rows: |
          select 'v1' as assumption_set, 'oc' as format, 'hitter' as player_type, 'ALL' as pos_group, cast(3.0 as double) as core_threshold_ros, 0 as stable_stream_hitters, 1 as stable_stream_pitchers, 1 as balanced_stream_hitters, 1 as balanced_stream_pitchers, 2 as aggressive_stream_hitters, 2 as aggressive_stream_pitchers, '1-1' as replacement_window_aggressive, '1-1' as replacement_window_base, '1-1' as replacement_window_conservative
          union all select 'v1', 'oc', 'hitter', 'C', cast(5.0 as double), 0, 1, 1, 1, 2, 2, '1-1', '1-1', '1-1'
          union all select 'v1', 'oc', 'pitcher', 'ALL', cast(3.0 as double), 0, 1, 1, 1, 2, 2, '1-1', '1-1', '1-1'
      - input: ref('ros_player_overrides')
        format: sql
        rows: |
          select cast(null as varchar) as assumption_set, cast(null as varchar) as nfbc_id, cast(null as varchar) as force_status where 1=0
      - input: ref('mart_rest_of_season_overall_rankings_oc')
        format: sql
        rows: |
          select 101 as id, 'OF' as pos_group, cast(10.0 as double) as value, cast(20.0 as double) as r, cast(0.0 as double) as hr, cast(0.0 as double) as rbi, cast(0.0 as double) as sb, cast(10.0 as double) as h, cast(40.0 as double) as ab, cast(null as double) as k, cast(null as double) as w, cast(null as double) as sv, cast(null as double) as er, cast(null as double) as ip, cast(null as double) as bb
          union all select 102, 'OF', cast(8.0 as double), cast(8.0 as double), 0, 0, 0, cast(4.0 as double), cast(16.0 as double), null, null, null, null, null, null
      - input: ref('mart_rest_of_season_overall_rankings_50s')
        format: sql
        rows: |
          select cast(null as integer) as id, cast(null as varchar) as pos_group, cast(null as double) as value, cast(null as double) as r, cast(null as double) as hr, cast(null as double) as rbi, cast(null as double) as sb, cast(null as double) as h, cast(null as double) as ab, cast(null as double) as k, cast(null as double) as w, cast(null as double) as sv, cast(null as double) as er, cast(null as double) as ip, cast(null as double) as bb where 1=0
      - input: ref('mart_weekly_lineup_inputs')
        format: sql
        rows: |
          select 'nolen_oc' as league, 'oc' as format, 'Dan Nolen' as owner, '101' as nfbc_id, 'hitter' as row_type, 0 as is_c_eligible, '2026-04-06' as week_of, cast(10.0 as double) as ros_value
          union all select 'nolen_oc', 'oc', 'Dan Nolen', '102', 'hitter', 0, '2026-04-06', cast(8.0 as double)
          union all select 'nolen_oc', 'oc', 'Dan Nolen', '201', 'pitcher', 0, '2026-04-06', cast(1.0 as double)
      - input: ref('mart_fa_replacement_curve')
        format: sql
        rows: |
          select 'oc' as format, 'hitter' as row_type, 1 as fa_rank, cast(3.0 as double) as mean_weekly_r, cast(0.0 as double) as mean_weekly_hr, cast(0.0 as double) as mean_weekly_rbi, cast(0.0 as double) as mean_weekly_sb, cast(1.0 as double) as mean_weekly_h, cast(4.0 as double) as mean_weekly_ab, cast(null as double) as mean_weekly_k, cast(null as double) as mean_weekly_w, cast(null as double) as mean_weekly_sv, cast(null as double) as mean_weekly_er, cast(null as double) as mean_weekly_ip, cast(null as double) as mean_weekly_ha, cast(null as double) as mean_weekly_bb
          union all select 'oc', 'pitcher', 1, null, null, null, null, null, null, cast(0.0 as double), cast(0.0 as double), cast(0.0 as double), cast(0.0 as double), cast(0.0 as double), cast(0.0 as double), cast(0.0 as double)
      - input: ref('src_nfbc_standings')
        format: sql
        rows: |
          select 'skip.csv' as _filename, cast(1.0 as double) as r, cast(1.0 as double) as hr, cast(1.0 as double) as rbi, cast(1.0 as double) as sb, cast(0.25 as double) as avg, cast(1.0 as double) as k, cast(1.0 as double) as w, cast(1.0 as double) as s, cast(3.0 as double) as era, cast(1.0 as double) as whip
      - input: ref('stg_nfbc_overall_category_long')
        format: sql
        rows: |
          {overall}
    expect:
      rows:
'''


REFS = [
    "league_config",
    "season_scoring_calendar",
    "league_roster_slots",
    "ros_core_assumptions",
    "ros_player_overrides",
    "mart_rest_of_season_overall_rankings_oc",
    "mart_rest_of_season_overall_rankings_50s",
    "mart_weekly_lineup_inputs",
    "mart_fa_replacement_curve",
    "src_nfbc_standings",
    "stg_nfbc_overall_category_long",
]


def given_sqls() -> dict[str, str]:
    return {
        "league_config": (
            "select 'nolen_oc' as league, 'oc' as format, "
            "890 as nfbc_overall_game_type_id, 1828 as nfbc_league_id"
        ),
        "season_scoring_calendar": (
            "select 2026 as season_year, 3 as scoring_periods, "
            "date '2026-03-27' as season_start_date"
        ),
        "league_roster_slots": (
            "select 'oc' as format, 'UTIL' as slot, 'hitter' as slot_group, "
            '2 as "count" union all select \'oc\', \'P\', \'pitcher\', 1'
        ),
        "ros_core_assumptions": (
            "select 'v1' as assumption_set, 'oc' as format, 'hitter' as player_type, "
            "'ALL' as pos_group, cast(3.0 as double) as core_threshold_ros, "
            "0 as stable_stream_hitters, 1 as stable_stream_pitchers, "
            "1 as balanced_stream_hitters, 1 as balanced_stream_pitchers, "
            "2 as aggressive_stream_hitters, 2 as aggressive_stream_pitchers, "
            "'1-1' as replacement_window_aggressive, "
            "'1-1' as replacement_window_base, "
            "'1-1' as replacement_window_conservative "
            "union all select 'v1', 'oc', 'hitter', 'C', cast(5.0 as double), "
            "0, 1, 1, 1, 2, 2, '1-1', '1-1', '1-1' "
            "union all select 'v1', 'oc', 'pitcher', 'ALL', cast(3.0 as double), "
            "0, 1, 1, 1, 2, 2, '1-1', '1-1', '1-1'"
        ),
        "ros_player_overrides": (
            "select cast(null as varchar) as assumption_set, "
            "cast(null as varchar) as nfbc_id, "
            "cast(null as varchar) as force_status where 1=0"
        ),
        "mart_rest_of_season_overall_rankings_oc": (
            "select 101 as id, 'OF' as pos_group, cast(10.0 as double) as value, "
            "cast(20.0 as double) as r, cast(0.0 as double) as hr, "
            "cast(0.0 as double) as rbi, cast(0.0 as double) as sb, "
            "cast(10.0 as double) as h, cast(40.0 as double) as ab, "
            "cast(null as double) as k, cast(null as double) as w, "
            "cast(null as double) as sv, cast(null as double) as er, "
            "cast(null as double) as ip, cast(null as double) as bb "
            "union all select 102, 'OF', cast(8.0 as double), cast(8.0 as double), "
            "0, 0, 0, cast(4.0 as double), cast(16.0 as double), "
            "null, null, null, null, null, null"
        ),
        "mart_rest_of_season_overall_rankings_50s": (
            "select cast(null as integer) as id, cast(null as varchar) as pos_group, "
            "cast(null as double) as value, cast(null as double) as r, "
            "cast(null as double) as hr, cast(null as double) as rbi, "
            "cast(null as double) as sb, cast(null as double) as h, "
            "cast(null as double) as ab, cast(null as double) as k, "
            "cast(null as double) as w, cast(null as double) as sv, "
            "cast(null as double) as er, cast(null as double) as ip, "
            "cast(null as double) as bb where 1=0"
        ),
        "mart_weekly_lineup_inputs": (
            "select 'nolen_oc' as league, 'oc' as format, 'Dan Nolen' as owner, "
            "'101' as nfbc_id, 'hitter' as row_type, 0 as is_c_eligible, "
            "'2026-04-06' as week_of, cast(10.0 as double) as ros_value "
            "union all select 'nolen_oc', 'oc', 'Dan Nolen', '102', 'hitter', 0, "
            "'2026-04-06', cast(8.0 as double) "
            "union all select 'nolen_oc', 'oc', 'Dan Nolen', '201', 'pitcher', 0, "
            "'2026-04-06', cast(1.0 as double)"
        ),
        "mart_fa_replacement_curve": (
            "select 'oc' as format, 'hitter' as row_type, 1 as fa_rank, "
            "cast(3.0 as double) as mean_weekly_r, cast(0.0 as double) as mean_weekly_hr, "
            "cast(0.0 as double) as mean_weekly_rbi, cast(0.0 as double) as mean_weekly_sb, "
            "cast(1.0 as double) as mean_weekly_h, cast(4.0 as double) as mean_weekly_ab, "
            "cast(null as double) as mean_weekly_k, cast(null as double) as mean_weekly_w, "
            "cast(null as double) as mean_weekly_sv, cast(null as double) as mean_weekly_er, "
            "cast(null as double) as mean_weekly_ip, cast(null as double) as mean_weekly_ha, "
            "cast(null as double) as mean_weekly_bb "
            "union all select 'oc', 'pitcher', 1, null, null, null, null, null, null, "
            "cast(0.0 as double), cast(0.0 as double), cast(0.0 as double), "
            "cast(0.0 as double), cast(0.0 as double), cast(0.0 as double), "
            "cast(0.0 as double)"
        ),
        "src_nfbc_standings": (
            "select 'skip.csv' as _filename, cast(1.0 as double) as r, "
            "cast(1.0 as double) as hr, cast(1.0 as double) as rbi, "
            "cast(1.0 as double) as sb, cast(0.25 as double) as avg, "
            "cast(1.0 as double) as k, cast(1.0 as double) as w, "
            "cast(1.0 as double) as s, cast(3.0 as double) as era, "
            "cast(1.0 as double) as whip"
        ),
        "stg_nfbc_overall_category_long": overall_sql(),
    }


def write_yaml(path: str) -> None:
    text = HEADER.format(overall=overall_sql())
    text += "\n".join(fmt_row(row) for row in EXPECT) + "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


if __name__ == "__main__":
    import sys

    out = (
        sys.argv[1]
        if sys.argv[1:]
        else "dbt/models/main/_unit_tests_projected_overall_finish.yml"
    )
    write_yaml(out)
    print(out)

