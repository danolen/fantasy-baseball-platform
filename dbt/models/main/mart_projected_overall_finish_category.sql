{{
    config(
        materialized='table'
    )
}}

-- Projected overall-finish category grain (#188).
-- One row per configured overall team / scenario / snapshot / category.
-- Remaining production is core ROS (players expected to start) plus
-- replacement for churn slots. The field is paced/blended; opponent
-- rosters are not projected. Output is a projected finish, not standings
-- for every contest team.
--
-- 50s is draft-and-hold: streaming_applicable is false, replacement is 0,
-- and all three scenario names still emit (identical frozen-roster rows).

{% set hist_oc = "NFBC OC 2025 Overall Standings.csv" %}
{% set hist_50s = "NFBC 50s 2025 Overall Standings.csv" %}

with configured as (
    select
        league,
        format,
        cast(nfbc_overall_game_type_id as int) as nfbc_overall_game_type_id,
        cast(nfbc_league_id as int) as nfbc_league_id
    from {{ ref('league_config') }}
    where nfbc_overall_game_type_id is not null
        and format in ('oc', '50s')
),

calendar as (
    select
        cast(season_year as int) as season_year,
        cast(scoring_periods as int) as season_scoring_periods,
        cast(season_start_date as date) as season_start_date
    from {{ ref('season_scoring_calendar') }}
),

categories as (
    select 'R' as category, true as higher_is_better, false as is_ratio
    union all select 'HR', true, false
    union all select 'RBI', true, false
    union all select 'SB', true, false
    union all select 'AVG', true, true
    union all select 'K', true, false
    union all select 'W', true, false
    union all select 'SV', true, false
    union all select 'ERA', false, true
    union all select 'WHIP', false, true
),

slot_needs as (
    select
        format,
        sum(case when slot_group = 'hitter' then "count" else 0 end) as n_hitter_slots,
        sum(case when slot_group = 'pitcher' then "count" else 0 end) as n_pitcher_slots
    from {{ ref('league_roster_slots') }}
    group by format
),

assumptions_wide as (
    select
        assumption_set,
        format,
        max(case when player_type = 'hitter' then cast(stable_stream_hitters as int) end) as stable_h,
        max(case when player_type = 'pitcher' then cast(stable_stream_pitchers as int) end) as stable_p,
        max(case when player_type = 'hitter' then cast(balanced_stream_hitters as int) end) as balanced_h,
        max(case when player_type = 'pitcher' then cast(balanced_stream_pitchers as int) end) as balanced_p,
        max(case when player_type = 'hitter' then cast(aggressive_stream_hitters as int) end) as aggressive_h,
        max(case when player_type = 'pitcher' then cast(aggressive_stream_pitchers as int) end) as aggressive_p,
        max(replacement_window_conservative) as window_stable,
        max(replacement_window_base) as window_balanced,
        max(replacement_window_aggressive) as window_aggressive,
        max(case when player_type = 'hitter' and pos_group = 'C' then core_threshold_ros end) as threshold_c,
        max(case when player_type = 'hitter' and pos_group = 'ALL' then core_threshold_ros end) as threshold_h,
        max(case when player_type = 'pitcher' and pos_group = 'ALL' then core_threshold_ros end) as threshold_p
    from {{ ref('ros_core_assumptions') }}
    where assumption_set = 'v1'
        and pos_group in ('ALL', 'C')
    group by assumption_set, format
),

faab_scenarios as (
    select
        assumption_set,
        format,
        'stable' as scenario,
        stable_h as n_stream_hitters,
        stable_p as n_stream_pitchers,
        window_stable as replacement_window,
        true as streaming_applicable,
        threshold_c,
        threshold_h,
        threshold_p
    from assumptions_wide
    union all
    select
        assumption_set,
        format,
        'balanced',
        balanced_h,
        balanced_p,
        window_balanced,
        true,
        threshold_c,
        threshold_h,
        threshold_p
    from assumptions_wide
    union all
    select
        assumption_set,
        format,
        'aggressive',
        aggressive_h,
        aggressive_p,
        window_aggressive,
        true,
        threshold_c,
        threshold_h,
        threshold_p
    from assumptions_wide
),

frozen_scenarios as (
    select
        'v1' as assumption_set,
        '50s' as format,
        scenario,
        0 as n_stream_hitters,
        0 as n_stream_pitchers,
        cast(null as varchar) as replacement_window,
        false as streaming_applicable,
        cast(null as double) as threshold_c,
        cast(null as double) as threshold_h,
        cast(null as double) as threshold_p
    from (
        select 'stable' as scenario
        union all select 'balanced'
        union all select 'aggressive'
    ) s
),

scenarios as (
    select * from faab_scenarios where format = 'oc'
    union all
    select * from frozen_scenarios
),

overrides as (
    select
        assumption_set,
        cast(nfbc_id as varchar) as nfbc_id,
        force_status
    from {{ ref('ros_player_overrides') }}
),

ros as (
    select
        'oc' as format,
        cast(id as varchar) as nfbc_id,
        pos_group,
        cast(value as double) as ros_value,
        cast(r as double) as r,
        cast(hr as double) as hr,
        cast(rbi as double) as rbi,
        cast(sb as double) as sb,
        cast(h as double) as h,
        cast(ab as double) as ab,
        cast(k as double) as k,
        cast(w as double) as w,
        cast(sv as double) as sv,
        cast(er as double) as er,
        cast(ip as double) as ip,
        cast(bb as double) as bb
    from {{ ref('mart_rest_of_season_overall_rankings_oc') }}
    union all
    select
        '50s',
        cast(id as varchar),
        pos_group,
        cast(value as double),
        cast(r as double),
        cast(hr as double),
        cast(rbi as double),
        cast(sb as double),
        cast(h as double),
        cast(ab as double),
        cast(k as double),
        cast(w as double),
        cast(sv as double),
        cast(er as double),
        cast(ip as double),
        cast(bb as double)
    from {{ ref('mart_rest_of_season_overall_rankings_50s') }}
),

latest_weeks as (
    select league, max(week_of) as week_of
    from {{ ref('mart_weekly_lineup_inputs') }}
    group by league
),

my_owners as (
    select
        li.league,
        li.owner,
        li.format,
        row_number() over (
            partition by li.league
            order by li.owner
        ) as rn
    from (
        select distinct
            inp.league,
            inp.owner,
            inp.format
        from {{ ref('mart_weekly_lineup_inputs') }} inp
        inner join configured c
            on c.league = inp.league
        where inp.owner is not null
            and trim(inp.owner) <> ''
            and lower(inp.owner) like '%nolen%'
    ) li
),

roster_base as (
    select
        li.league,
        li.format,
        li.owner,
        cast(li.nfbc_id as varchar) as nfbc_id,
        li.row_type,
        li.is_c_eligible,
        li.week_of,
        coalesce(ros.ros_value, li.ros_value) as ros_value,
        case when li.row_type = 'hitter' then coalesce(ros.r, 0.0) else 0.0 end as r,
        case when li.row_type = 'hitter' then coalesce(ros.hr, 0.0) else 0.0 end as hr,
        case when li.row_type = 'hitter' then coalesce(ros.rbi, 0.0) else 0.0 end as rbi,
        case when li.row_type = 'hitter' then coalesce(ros.sb, 0.0) else 0.0 end as sb,
        case when li.row_type = 'hitter' then coalesce(ros.h, 0.0) else 0.0 end as h,
        case when li.row_type = 'hitter' then coalesce(ros.ab, 0.0) else 0.0 end as ab,
        case when li.row_type = 'pitcher' then coalesce(ros.k, 0.0) else 0.0 end as k,
        case when li.row_type = 'pitcher' then coalesce(ros.w, 0.0) else 0.0 end as w,
        case when li.row_type = 'pitcher' then coalesce(ros.sv, 0.0) else 0.0 end as sv,
        case when li.row_type = 'pitcher' then coalesce(ros.er, 0.0) else 0.0 end as er,
        case when li.row_type = 'pitcher' then coalesce(ros.ip, 0.0) else 0.0 end as ip,
        case when li.row_type = 'pitcher' then coalesce(ros.h, 0.0) else 0.0 end as ha,
        case when li.row_type = 'pitcher' then coalesce(ros.bb, 0.0) else 0.0 end as bb
    from {{ ref('mart_weekly_lineup_inputs') }} li
    inner join latest_weeks w
        on w.league = li.league
        and w.week_of = li.week_of
    inner join my_owners mo
        on mo.league = li.league
        and mo.owner = li.owner
        and mo.rn = 1
    left join ros
        on ros.format = li.format
        and ros.nfbc_id = cast(li.nfbc_id as varchar)
        and (
            (li.row_type = 'hitter' and coalesce(ros.pos_group, '') <> 'P')
            or (li.row_type = 'pitcher' and ros.pos_group = 'P')
        )
),

roster_classified as (
    select
        rb.*,
        sc.assumption_set,
        sc.scenario,
        sc.n_stream_hitters,
        sc.n_stream_pitchers,
        sc.replacement_window,
        sc.streaming_applicable,
        sn.n_hitter_slots,
        sn.n_pitcher_slots,
        ov.force_status,
        case
            when not sc.streaming_applicable then true
            when ov.force_status = 'core' then true
            when ov.force_status = 'stream' then false
            when rb.row_type = 'hitter'
                and rb.is_c_eligible = 1
                and rb.ros_value is not null
                and rb.ros_value >= coalesce(sc.threshold_c, sc.threshold_h)
                then true
            when rb.row_type = 'hitter'
                and coalesce(rb.is_c_eligible, 0) = 0
                and rb.ros_value is not null
                and rb.ros_value >= sc.threshold_h
                then true
            when rb.row_type = 'pitcher'
                and rb.ros_value is not null
                and rb.ros_value >= sc.threshold_p
                then true
            else false
        end as is_core
    from roster_base rb
    inner join scenarios sc
        on sc.format = rb.format
    inner join slot_needs sn
        on sn.format = rb.format
    left join overrides ov
        on ov.assumption_set = sc.assumption_set
        and ov.nfbc_id = rb.nfbc_id
),

core_ranked as (
    select
        rc.*,
        row_number() over (
            partition by rc.league, rc.owner, rc.scenario, rc.row_type
            order by coalesce(rc.ros_value, -1e9) desc, rc.nfbc_id
        ) as core_rank
    from roster_classified rc
    where rc.is_core
),

core_starters as (
    select
        cr.*,
        case
            when cr.row_type = 'hitter'
                then cr.core_rank <= greatest(cr.n_hitter_slots - cr.n_stream_hitters, 0)
            else cr.core_rank <= greatest(cr.n_pitcher_slots - cr.n_stream_pitchers, 0)
        end as is_core_starter
    from core_ranked cr
),

core_agg as (
    select
        league,
        format,
        owner,
        week_of,
        assumption_set,
        scenario,
        streaming_applicable,
        n_stream_hitters,
        n_stream_pitchers,
        replacement_window,
        n_hitter_slots,
        n_pitcher_slots,
        sum(case when is_core_starter and row_type = 'hitter' then 1 else 0 end) as n_core_starters_hitters,
        sum(case when is_core_starter and row_type = 'pitcher' then 1 else 0 end) as n_core_starters_pitchers,
        sum(case when is_core_starter then r else 0 end) as core_r,
        sum(case when is_core_starter then hr else 0 end) as core_hr,
        sum(case when is_core_starter then rbi else 0 end) as core_rbi,
        sum(case when is_core_starter then sb else 0 end) as core_sb,
        sum(case when is_core_starter then h else 0 end) as core_h,
        sum(case when is_core_starter then ab else 0 end) as core_ab,
        sum(case when is_core_starter then k else 0 end) as core_k,
        sum(case when is_core_starter then w else 0 end) as core_w,
        sum(case when is_core_starter then sv else 0 end) as core_sv,
        sum(case when is_core_starter then er else 0 end) as core_er,
        sum(case when is_core_starter then ip else 0 end) as core_ip,
        sum(case when is_core_starter then ha else 0 end) as core_ha,
        sum(case when is_core_starter then bb else 0 end) as core_bb
    from core_starters
    group by
        league,
        format,
        owner,
        week_of,
        assumption_set,
        scenario,
        streaming_applicable,
        n_stream_hitters,
        n_stream_pitchers,
        replacement_window,
        n_hitter_slots,
        n_pitcher_slots
),

-- Teams with a Nolen roster but no core starters still need scenario rows.
roster_scenario_spine as (
    select distinct
        rb.league,
        rb.format,
        rb.owner,
        rb.week_of,
        sc.assumption_set,
        sc.scenario,
        sc.streaming_applicable,
        sc.n_stream_hitters,
        sc.n_stream_pitchers,
        sc.replacement_window,
        sn.n_hitter_slots,
        sn.n_pitcher_slots
    from roster_base rb
    inner join scenarios sc
        on sc.format = rb.format
    inner join slot_needs sn
        on sn.format = rb.format
),

core_filled as (
    select
        sp.league,
        sp.format,
        sp.owner,
        sp.week_of,
        sp.assumption_set,
        sp.scenario,
        sp.streaming_applicable,
        sp.n_stream_hitters,
        sp.n_stream_pitchers,
        sp.replacement_window,
        sp.n_hitter_slots,
        sp.n_pitcher_slots,
        coalesce(ca.n_core_starters_hitters, 0) as n_core_starters_hitters,
        coalesce(ca.n_core_starters_pitchers, 0) as n_core_starters_pitchers,
        coalesce(ca.core_r, 0.0) as core_r,
        coalesce(ca.core_hr, 0.0) as core_hr,
        coalesce(ca.core_rbi, 0.0) as core_rbi,
        coalesce(ca.core_sb, 0.0) as core_sb,
        coalesce(ca.core_h, 0.0) as core_h,
        coalesce(ca.core_ab, 0.0) as core_ab,
        coalesce(ca.core_k, 0.0) as core_k,
        coalesce(ca.core_w, 0.0) as core_w,
        coalesce(ca.core_sv, 0.0) as core_sv,
        coalesce(ca.core_er, 0.0) as core_er,
        coalesce(ca.core_ip, 0.0) as core_ip,
        coalesce(ca.core_ha, 0.0) as core_ha,
        coalesce(ca.core_bb, 0.0) as core_bb,
        case
            when not sp.streaming_applicable then 0
            else sp.n_hitter_slots - coalesce(ca.n_core_starters_hitters, 0)
        end as n_repl_hitters,
        case
            when not sp.streaming_applicable then 0
            else sp.n_pitcher_slots - coalesce(ca.n_core_starters_pitchers, 0)
        end as n_repl_pitchers
    from roster_scenario_spine sp
    left join core_agg ca
        on ca.league = sp.league
        and ca.owner = sp.owner
        and ca.scenario = sp.scenario
),

repl_weekly as (
    select
        sc.assumption_set,
        sc.format,
        sc.scenario,
        avg(case when c.row_type = 'hitter' then c.mean_weekly_r end) as h_r,
        avg(case when c.row_type = 'hitter' then c.mean_weekly_hr end) as h_hr,
        avg(case when c.row_type = 'hitter' then c.mean_weekly_rbi end) as h_rbi,
        avg(case when c.row_type = 'hitter' then c.mean_weekly_sb end) as h_sb,
        avg(case when c.row_type = 'hitter' then c.mean_weekly_h end) as h_h,
        avg(case when c.row_type = 'hitter' then c.mean_weekly_ab end) as h_ab,
        avg(case when c.row_type = 'pitcher' then c.mean_weekly_k end) as p_k,
        avg(case when c.row_type = 'pitcher' then c.mean_weekly_w end) as p_w,
        avg(case when c.row_type = 'pitcher' then c.mean_weekly_sv end) as p_sv,
        avg(case when c.row_type = 'pitcher' then c.mean_weekly_er end) as p_er,
        avg(case when c.row_type = 'pitcher' then c.mean_weekly_ip end) as p_ip,
        avg(case when c.row_type = 'pitcher' then c.mean_weekly_ha end) as p_ha,
        avg(case when c.row_type = 'pitcher' then c.mean_weekly_bb end) as p_bb
    from scenarios sc
    left join {{ ref('mart_fa_replacement_curve') }} c
        on c.format = sc.format
        and sc.streaming_applicable
        and sc.replacement_window is not null
        and c.fa_rank >= try_cast(split_part(sc.replacement_window, '-', 1) as integer)
        and c.fa_rank <= try_cast(split_part(sc.replacement_window, '-', 2) as integer)
    group by
        sc.assumption_set,
        sc.format,
        sc.scenario
),

my_team_keys as (
    select
        s.contest_key,
        s.source_league_key,
        s.format,
        s.nfbc_overall_game_type_id,
        s.snapshot_date,
        s.is_latest_snapshot,
        s.team_key,
        s.owner,
        s.team,
        s.nfbc_league_id,
        s.overall_rank,
        s.overall_points,
        row_number() over (
            partition by s.contest_key, s.snapshot_date
            order by s.team_key
        ) as rn
    from (
        select distinct
            ol.contest_key,
            ol.source_league_key,
            ol.format,
            ol.nfbc_overall_game_type_id,
            ol.snapshot_date,
            ol.is_latest_snapshot,
            ol.team_key,
            ol.owner,
            ol.team,
            ol.nfbc_league_id,
            ol.overall_rank,
            ol.overall_points
        from {{ ref('stg_nfbc_overall_category_long') }} ol
        inner join configured c
            on c.league = ol.contest_key
        where ol.is_latest_snapshot
            and (
                lower(coalesce(ol.team, '')) like '%nolen%'
                or lower(coalesce(ol.owner, '')) like '%nolen%'
            )
    ) s
),

my_team as (
    select *
    from my_team_keys
    where rn = 1
),

current_wide as (
    select
        mt.contest_key,
        mt.format,
        mt.nfbc_overall_game_type_id,
        mt.snapshot_date,
        mt.is_latest_snapshot,
        mt.team_key,
        mt.owner,
        mt.team,
        mt.nfbc_league_id,
        mt.overall_rank as current_overall_rank,
        mt.overall_points as current_overall_points,
        max(case when ol.category = 'R' then ol.raw_stat end) as current_r,
        max(case when ol.category = 'HR' then ol.raw_stat end) as current_hr,
        max(case when ol.category = 'RBI' then ol.raw_stat end) as current_rbi,
        max(case when ol.category = 'SB' then ol.raw_stat end) as current_sb,
        max(case when ol.category = 'AVG' then ol.raw_stat end) as current_avg,
        max(case when ol.category = 'K' then ol.raw_stat end) as current_k,
        max(case when ol.category = 'W' then ol.raw_stat end) as current_w,
        max(case when ol.category = 'SV' then ol.raw_stat end) as current_sv,
        max(case when ol.category = 'ERA' then ol.raw_stat end) as current_era,
        max(case when ol.category = 'WHIP' then ol.raw_stat end) as current_whip,
        max(ol.volume_h) as current_h,
        max(ol.volume_ab) as current_ab,
        max(ol.volume_er) as current_er,
        max(ol.volume_ip) as current_ip,
        max(ol.volume_bb_h) as current_bb_h,
        max(case when ol.category = 'R' then ol.category_points end) as current_r_pts,
        max(case when ol.category = 'HR' then ol.category_points end) as current_hr_pts,
        max(case when ol.category = 'RBI' then ol.category_points end) as current_rbi_pts,
        max(case when ol.category = 'SB' then ol.category_points end) as current_sb_pts,
        max(case when ol.category = 'AVG' then ol.category_points end) as current_avg_pts,
        max(case when ol.category = 'K' then ol.category_points end) as current_k_pts,
        max(case when ol.category = 'W' then ol.category_points end) as current_w_pts,
        max(case when ol.category = 'SV' then ol.category_points end) as current_sv_pts,
        max(case when ol.category = 'ERA' then ol.category_points end) as current_era_pts,
        max(case when ol.category = 'WHIP' then ol.category_points end) as current_whip_pts
    from my_team mt
    inner join {{ ref('stg_nfbc_overall_category_long') }} ol
        on ol.contest_key = mt.contest_key
        and ol.snapshot_date = mt.snapshot_date
        and ol.team_key = mt.team_key
    group by
        mt.contest_key,
        mt.format,
        mt.nfbc_overall_game_type_id,
        mt.snapshot_date,
        mt.is_latest_snapshot,
        mt.team_key,
        mt.owner,
        mt.team,
        mt.nfbc_league_id,
        mt.overall_rank,
        mt.overall_points
),

paced as (
    select
        cw.*,
        cal.season_scoring_periods,
        cal.season_start_date,
        greatest(
            1,
            date_diff('week', cal.season_start_date, cw.snapshot_date) + 1
        ) as weeks_elapsed,
        greatest(
            1,
            cal.season_scoring_periods
            - greatest(1, date_diff('week', cal.season_start_date, cw.snapshot_date) + 1)
        ) as weeks_remaining,
        least(
            greatest(
                greatest(1, date_diff('week', cal.season_start_date, cw.snapshot_date) + 1)
                * 1.0 / cal.season_scoring_periods,
                0.0
            ),
            1.0
        ) as season_completion
    from current_wide cw
    inner join calendar cal
        on cal.season_year = year(cw.snapshot_date)
),

our_projection as (
    select
        p.contest_key,
        p.format,
        p.nfbc_overall_game_type_id,
        p.snapshot_date,
        p.is_latest_snapshot,
        p.team_key,
        p.owner as standings_owner,
        p.team,
        p.nfbc_league_id,
        p.current_overall_rank,
        p.current_overall_points,
        p.weeks_elapsed,
        p.weeks_remaining,
        p.season_completion,
        p.season_completion as current_weight,
        cf.owner as roster_owner,
        cf.week_of,
        cf.assumption_set,
        cf.scenario,
        cf.streaming_applicable,
        cf.n_stream_hitters,
        cf.n_stream_pitchers,
        cf.n_repl_hitters,
        cf.n_repl_pitchers,
        cf.n_core_starters_hitters,
        cf.n_core_starters_pitchers,
        cf.replacement_window,
        p.current_r,
        p.current_hr,
        p.current_rbi,
        p.current_sb,
        p.current_avg,
        p.current_k,
        p.current_w,
        p.current_sv,
        p.current_era,
        p.current_whip,
        p.current_h,
        p.current_ab,
        p.current_er,
        p.current_ip,
        p.current_bb_h,
        p.current_r_pts,
        p.current_hr_pts,
        p.current_rbi_pts,
        p.current_sb_pts,
        p.current_avg_pts,
        p.current_k_pts,
        p.current_w_pts,
        p.current_sv_pts,
        p.current_era_pts,
        p.current_whip_pts,
        cf.core_r,
        cf.core_hr,
        cf.core_rbi,
        cf.core_sb,
        cf.core_h,
        cf.core_ab,
        cf.core_k,
        cf.core_w,
        cf.core_sv,
        cf.core_er,
        cf.core_ip,
        cf.core_ha,
        cf.core_bb,
        coalesce(cf.n_repl_hitters, 0) * coalesce(rw.h_r, 0.0) * p.weeks_remaining as repl_r,
        coalesce(cf.n_repl_hitters, 0) * coalesce(rw.h_hr, 0.0) * p.weeks_remaining as repl_hr,
        coalesce(cf.n_repl_hitters, 0) * coalesce(rw.h_rbi, 0.0) * p.weeks_remaining as repl_rbi,
        coalesce(cf.n_repl_hitters, 0) * coalesce(rw.h_sb, 0.0) * p.weeks_remaining as repl_sb,
        coalesce(cf.n_repl_hitters, 0) * coalesce(rw.h_h, 0.0) * p.weeks_remaining as repl_h,
        coalesce(cf.n_repl_hitters, 0) * coalesce(rw.h_ab, 0.0) * p.weeks_remaining as repl_ab,
        coalesce(cf.n_repl_pitchers, 0) * coalesce(rw.p_k, 0.0) * p.weeks_remaining as repl_k,
        coalesce(cf.n_repl_pitchers, 0) * coalesce(rw.p_w, 0.0) * p.weeks_remaining as repl_w,
        coalesce(cf.n_repl_pitchers, 0) * coalesce(rw.p_sv, 0.0) * p.weeks_remaining as repl_sv,
        coalesce(cf.n_repl_pitchers, 0) * coalesce(rw.p_er, 0.0) * p.weeks_remaining as repl_er,
        coalesce(cf.n_repl_pitchers, 0) * coalesce(rw.p_ip, 0.0) * p.weeks_remaining as repl_ip,
        coalesce(cf.n_repl_pitchers, 0) * coalesce(rw.p_ha, 0.0) * p.weeks_remaining as repl_ha,
        coalesce(cf.n_repl_pitchers, 0) * coalesce(rw.p_bb, 0.0) * p.weeks_remaining as repl_bb
    from paced p
    left join core_filled cf
        on cf.league = p.contest_key
        and cf.format = p.format
    left join repl_weekly rw
        on rw.format = p.format
        and rw.scenario = cf.scenario
        and rw.assumption_set = cf.assumption_set
),

our_long as (
    select
        op.*,
        cat.category,
        cat.higher_is_better,
        cat.is_ratio,
        case cat.category
            when 'R' then op.current_r
            when 'HR' then op.current_hr
            when 'RBI' then op.current_rbi
            when 'SB' then op.current_sb
            when 'AVG' then op.current_avg
            when 'K' then op.current_k
            when 'W' then op.current_w
            when 'SV' then op.current_sv
            when 'ERA' then op.current_era
            when 'WHIP' then op.current_whip
        end as current_raw,
        case cat.category
            when 'AVG' then op.current_h
            when 'ERA' then op.current_er
            when 'WHIP' then op.current_bb_h
        end as current_num,
        case cat.category
            when 'AVG' then op.current_ab
            when 'ERA' then op.current_ip
            when 'WHIP' then op.current_ip
        end as current_den,
        case cat.category
            when 'R' then op.core_r
            when 'HR' then op.core_hr
            when 'RBI' then op.core_rbi
            when 'SB' then op.core_sb
            when 'K' then op.core_k
            when 'W' then op.core_w
            when 'SV' then op.core_sv
            else 0.0
        end as remaining_core_raw,
        case cat.category
            when 'AVG' then op.core_h
            when 'ERA' then op.core_er
            when 'WHIP' then op.core_ha + op.core_bb
        end as remaining_core_num,
        case cat.category
            when 'AVG' then op.core_ab
            when 'ERA' then op.core_ip
            when 'WHIP' then op.core_ip
        end as remaining_core_den,
        case cat.category
            when 'R' then op.repl_r
            when 'HR' then op.repl_hr
            when 'RBI' then op.repl_rbi
            when 'SB' then op.repl_sb
            when 'K' then op.repl_k
            when 'W' then op.repl_w
            when 'SV' then op.repl_sv
            else 0.0
        end as remaining_replacement_raw,
        case cat.category
            when 'AVG' then op.repl_h
            when 'ERA' then op.repl_er
            when 'WHIP' then op.repl_ha + op.repl_bb
        end as remaining_replacement_num,
        case cat.category
            when 'AVG' then op.repl_ab
            when 'ERA' then op.repl_ip
            when 'WHIP' then op.repl_ip
        end as remaining_replacement_den,
        case cat.category
            when 'R' then op.current_r_pts
            when 'HR' then op.current_hr_pts
            when 'RBI' then op.current_rbi_pts
            when 'SB' then op.current_sb_pts
            when 'AVG' then op.current_avg_pts
            when 'K' then op.current_k_pts
            when 'W' then op.current_w_pts
            when 'SV' then op.current_sv_pts
            when 'ERA' then op.current_era_pts
            when 'WHIP' then op.current_whip_pts
        end as current_category_points
    from our_projection op
    cross join categories cat
    where op.scenario is not null
),

our_projected as (
    select
        ol.*,
        case
            when not ol.is_ratio
                then coalesce(ol.remaining_core_raw, 0.0)
                    + coalesce(ol.remaining_replacement_raw, 0.0)
        end as remaining_raw,
        case
            when ol.is_ratio
                then coalesce(ol.remaining_core_num, 0.0)
                    + coalesce(ol.remaining_replacement_num, 0.0)
        end as remaining_num,
        case
            when ol.is_ratio
                then coalesce(ol.remaining_core_den, 0.0)
                    + coalesce(ol.remaining_replacement_den, 0.0)
        end as remaining_den,
        case
            when not ol.is_ratio
                then coalesce(ol.current_raw, 0.0)
                    + coalesce(ol.remaining_core_raw, 0.0)
                    + coalesce(ol.remaining_replacement_raw, 0.0)
            when ol.category = 'AVG'
                then (
                    coalesce(ol.current_num, 0.0)
                    + coalesce(ol.remaining_core_num, 0.0)
                    + coalesce(ol.remaining_replacement_num, 0.0)
                ) / nullif(
                    coalesce(ol.current_den, 0.0)
                    + coalesce(ol.remaining_core_den, 0.0)
                    + coalesce(ol.remaining_replacement_den, 0.0),
                    0.0
                )
            when ol.category = 'ERA'
                then 9.0 * (
                    coalesce(ol.current_num, 0.0)
                    + coalesce(ol.remaining_core_num, 0.0)
                    + coalesce(ol.remaining_replacement_num, 0.0)
                ) / nullif(
                    coalesce(ol.current_den, 0.0)
                    + coalesce(ol.remaining_core_den, 0.0)
                    + coalesce(ol.remaining_replacement_den, 0.0),
                    0.0
                )
            when ol.category = 'WHIP'
                then (
                    coalesce(ol.current_num, 0.0)
                    + coalesce(ol.remaining_core_num, 0.0)
                    + coalesce(ol.remaining_replacement_num, 0.0)
                ) / nullif(
                    coalesce(ol.current_den, 0.0)
                    + coalesce(ol.remaining_core_den, 0.0)
                    + coalesce(ol.remaining_replacement_den, 0.0),
                    0.0
                )
        end as projected_final
    from our_long ol
),

-- Completed-season percentile curve. Quality percentile is high = better
-- (counting/AVG ascending raw; ERA/WHIP descending raw).
hist_unpivoted as (
    select
        case
            when _filename = '{{ hist_oc }}' then 'oc'
            when _filename = '{{ hist_50s }}' then '50s'
        end as format,
        category,
        val,
        case
            when category in ('ERA', 'WHIP') then -val
            else val
        end as sort_val
    from (
        select _filename, 'R' as category, cast(r as double) as val
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'HR', cast(hr as double)
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'RBI', cast(rbi as double)
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'SB', cast(sb as double)
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'AVG', cast(avg as double)
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'K', cast(k as double)
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'W', cast(w as double)
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'SV', cast(s as double)
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'ERA', cast(era as double)
        from {{ ref('src_nfbc_standings') }}
        union all select _filename, 'WHIP', cast(whip as double)
        from {{ ref('src_nfbc_standings') }}
    ) u
    where _filename in ('{{ hist_oc }}', '{{ hist_50s }}')
        and val is not null
),

hist_with_pct as (
    select
        format,
        category,
        val,
        round(
            percent_rank() over (
                partition by format, category
                order by sort_val
            ),
            2
        ) as pct_key
    from hist_unpivoted
),

hist_curve as (
    select
        format,
        category,
        pct_key,
        avg(val) as hist_raw
    from hist_with_pct
    group by format, category, pct_key
),

other_current as (
    select
        ol.contest_key,
        ol.format,
        ol.snapshot_date,
        ol.team_key,
        ol.category,
        ol.higher_is_better,
        ol.is_ratio,
        ol.raw_stat,
        p.season_completion,
        p.season_completion as current_weight,
        case
            when ol.is_ratio then ol.raw_stat
            else ol.raw_stat / nullif(p.season_completion, 0.0)
        end as annualized_raw,
        round(
            percent_rank() over (
                partition by ol.contest_key, ol.snapshot_date, ol.category
                order by case when ol.higher_is_better then ol.raw_stat else -ol.raw_stat end
            ),
            2
        ) as pct_key
    from {{ ref('stg_nfbc_overall_category_long') }} ol
    inner join paced p
        on p.contest_key = ol.contest_key
        and p.snapshot_date = ol.snapshot_date
    inner join my_team mt
        on mt.contest_key = ol.contest_key
        and mt.snapshot_date = ol.snapshot_date
    where ol.is_latest_snapshot
        and ol.team_key <> mt.team_key
),

other_forecast as (
    select
        oc.contest_key,
        oc.format,
        oc.snapshot_date,
        oc.team_key,
        oc.category,
        oc.higher_is_better,
        oc.is_ratio,
        oc.season_completion,
        oc.current_weight,
        oc.annualized_raw,
        oc.pct_key,
        hc.hist_raw,
        coalesce(hc.hist_raw, oc.annualized_raw) as hist_field_raw,
        oc.annualized_raw as current_field_raw,
        case
            when hc.hist_raw is null then oc.annualized_raw
            else (1.0 - oc.current_weight) * hc.hist_raw
                + oc.current_weight * oc.annualized_raw
        end as blended_raw
    from other_current oc
    left join hist_curve hc
        on hc.format = oc.format
        and hc.category = oc.category
        and hc.pct_key = oc.pct_key
),

nearest_cutline as (
    select
        op.contest_key,
        op.snapshot_date,
        op.scenario,
        op.category,
        abs(of.hist_field_raw - of.current_field_raw) as uncertainty_raw,
        row_number() over (
            partition by
                op.contest_key, op.snapshot_date, op.scenario, op.category
            order by abs(of.blended_raw - op.projected_final), of.team_key
        ) as rn
    from our_projected op
    inner join other_forecast of
        on of.contest_key = op.contest_key
        and of.snapshot_date = op.snapshot_date
        and of.category = op.category
),

scenario_list as (
    select distinct
        contest_key,
        snapshot_date,
        scenario
    from our_projected
),

field_raw as (
    select
        of.contest_key,
        of.format,
        of.snapshot_date,
        sl.scenario,
        of.team_key,
        of.category,
        of.higher_is_better,
        of.is_ratio,
        of.blended_raw as forecast_raw,
        of.hist_field_raw,
        of.current_field_raw,
        false as is_my_team
    from other_forecast of
    inner join scenario_list sl
        on sl.contest_key = of.contest_key
        and sl.snapshot_date = of.snapshot_date
    union all
    select
        op.contest_key,
        op.format,
        op.snapshot_date,
        op.scenario,
        op.team_key,
        op.category,
        op.higher_is_better,
        op.is_ratio,
        op.projected_final as forecast_raw,
        op.projected_final as hist_field_raw,
        op.projected_final as current_field_raw,
        true as is_my_team
    from our_projected op
),

scored as (
    select
        fr.*,
        count(*) over (
            partition by contest_key, snapshot_date, scenario, category
        ) as n_teams,
        rank() over (
            partition by contest_key, snapshot_date, scenario, category
            order by
                case when higher_is_better then forecast_raw end desc,
                case when not higher_is_better then forecast_raw end asc
        ) as rk_blend,
        rank() over (
            partition by contest_key, snapshot_date, scenario, category
            order by
                case when higher_is_better then hist_field_raw end desc,
                case when not higher_is_better then hist_field_raw end asc
        ) as rk_hist,
        rank() over (
            partition by contest_key, snapshot_date, scenario, category
            order by
                case when higher_is_better then current_field_raw end desc,
                case when not higher_is_better then current_field_raw end asc
        ) as rk_current
    from field_raw fr
),

-- Tie groups share the average of the occupied rank points
-- (n_teams + 1 - rank), matching NFBC half-points.
scored_pts as (
    select
        s.*,
        s.n_teams + 1.0 - (
            s.rk_blend
            + s.rk_blend
            + count(*) over (
                partition by
                    s.contest_key, s.snapshot_date, s.scenario, s.category, s.forecast_raw
            )
            - 1.0
        ) / 2.0 as category_points_blend,
        s.n_teams + 1.0 - (
            s.rk_hist
            + s.rk_hist
            + count(*) over (
                partition by
                    s.contest_key, s.snapshot_date, s.scenario, s.category, s.hist_field_raw
            )
            - 1.0
        ) / 2.0 as category_points_hist,
        s.n_teams + 1.0 - (
            s.rk_current
            + s.rk_current
            + count(*) over (
                partition by
                    s.contest_key, s.snapshot_date, s.scenario, s.category, s.current_field_raw
            )
            - 1.0
        ) / 2.0 as category_points_current
    from scored s
),

my_scored as (
    select *
    from scored_pts
    where is_my_team
),

overall_field as (
    select
        contest_key,
        snapshot_date,
        scenario,
        team_key,
        is_my_team,
        sum(category_points_blend) as overall_points_blend,
        sum(category_points_hist) as overall_points_hist,
        sum(category_points_current) as overall_points_current
    from scored_pts
    group by contest_key, snapshot_date, scenario, team_key, is_my_team
),

overall_ranked as (
    select
        ofl.*,
        rank() over (
            partition by contest_key, snapshot_date, scenario
            order by overall_points_blend desc
        ) as overall_rank_blend,
        rank() over (
            partition by contest_key, snapshot_date, scenario
            order by overall_points_hist desc
        ) as overall_rank_hist,
        rank() over (
            partition by contest_key, snapshot_date, scenario
            order by overall_points_current desc
        ) as overall_rank_current
    from overall_field ofl
),

my_overall as (
    select *
    from overall_ranked
    where is_my_team
)

select
    op.contest_key as league,
    op.format,
    op.contest_key,
    op.nfbc_overall_game_type_id,
    op.snapshot_date,
    op.is_latest_snapshot,
    op.week_of,
    op.team_key,
    op.standings_owner as owner,
    op.team,
    op.nfbc_league_id,
    op.assumption_set,
    op.scenario,
    op.streaming_applicable,
    op.n_stream_hitters,
    op.n_stream_pitchers,
    op.n_repl_hitters,
    op.n_repl_pitchers,
    op.n_core_starters_hitters,
    op.n_core_starters_pitchers,
    op.replacement_window,
    op.weeks_elapsed,
    op.weeks_remaining,
    op.season_completion,
    op.current_weight,
    op.category,
    op.higher_is_better,
    op.is_ratio,
    op.current_raw,
    op.current_num,
    op.current_den,
    op.current_category_points,
    op.current_overall_points,
    op.current_overall_rank,
    case when op.is_ratio then null else op.remaining_core_raw end as remaining_core_raw,
    op.remaining_core_num,
    op.remaining_core_den,
    case when op.is_ratio then null else op.remaining_replacement_raw end as remaining_replacement_raw,
    op.remaining_replacement_num,
    op.remaining_replacement_den,
    op.remaining_raw,
    op.remaining_num,
    op.remaining_den,
    op.projected_final,
    ms.category_points_blend as projected_category_points,
    ms.category_points_hist as projected_category_points_hist_field,
    ms.category_points_current as projected_category_points_current_field,
    nc.uncertainty_raw,
    case
        when op.is_ratio then 'blend_current_and_2025_rate_no_annualize'
        else 'blend_annualized_current_and_2025_percentile'
    end as cutline_method,
    mo.overall_points_blend as projected_overall_points,
    mo.overall_rank_blend as projected_overall_rank,
    least(mo.overall_rank_hist, mo.overall_rank_current) as projected_rank_low,
    greatest(mo.overall_rank_hist, mo.overall_rank_current) as projected_rank_high,
    least(mo.overall_points_hist, mo.overall_points_current) as projected_points_low,
    greatest(mo.overall_points_hist, mo.overall_points_current) as projected_points_high,
    'projected overall finish' as output_kind
from our_projected op
inner join my_scored ms
    on ms.contest_key = op.contest_key
    and ms.snapshot_date = op.snapshot_date
    and ms.scenario = op.scenario
    and ms.category = op.category
inner join my_overall mo
    on mo.contest_key = op.contest_key
    and mo.snapshot_date = op.snapshot_date
    and mo.scenario = op.scenario
left join nearest_cutline nc
    on nc.contest_key = op.contest_key
    and nc.snapshot_date = op.snapshot_date
    and nc.scenario = op.scenario
    and nc.category = op.category
    and nc.rn = 1
