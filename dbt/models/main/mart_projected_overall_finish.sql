{{
    config(
        materialized='table'
    )
}}

-- Projected overall finish (#188). One row per configured overall team /
-- scenario / snapshot. Category detail lives in
-- mart_projected_overall_finish_category. This is a projected finish for
-- the configured Nolen OC and 50s entries, not projected standings for
-- every contest team.

select
    league,
    format,
    contest_key,
    nfbc_overall_game_type_id,
    snapshot_date,
    is_latest_snapshot,
    week_of,
    team_key,
    owner,
    team,
    nfbc_league_id,
    assumption_set,
    scenario,
    streaming_applicable,
    max(n_stream_hitters) as n_stream_hitters,
    max(n_stream_pitchers) as n_stream_pitchers,
    max(n_repl_hitters) as n_repl_hitters,
    max(n_repl_pitchers) as n_repl_pitchers,
    max(n_core_starters_hitters) as n_core_starters_hitters,
    max(n_core_starters_pitchers) as n_core_starters_pitchers,
    max(replacement_window) as replacement_window,
    max(weeks_elapsed) as weeks_elapsed,
    max(weeks_remaining) as weeks_remaining,
    max(season_completion) as season_completion,
    max(current_weight) as current_weight,
    max(current_overall_points) as current_overall_points,
    max(current_overall_rank) as current_overall_rank,
    max(projected_overall_points) as projected_overall_points,
    max(projected_overall_rank) as projected_overall_rank,
    max(projected_rank_low) as projected_rank_low,
    max(projected_rank_high) as projected_rank_high,
    max(projected_points_low) as projected_points_low,
    max(projected_points_high) as projected_points_high,
    max(output_kind) as output_kind
from {{ ref('mart_projected_overall_finish_category') }}
group by
    league,
    format,
    contest_key,
    nfbc_overall_game_type_id,
    snapshot_date,
    is_latest_snapshot,
    week_of,
    team_key,
    owner,
    team,
    nfbc_league_id,
    assumption_set,
    scenario,
    streaming_applicable
