{{
    config(
        materialized='table'
    )
}}

-- ROS SGP calibration with 2026 standings (#184).
--
-- Blends stable completed-season (2025) SGP slopes with current-season slopes
-- reconstructed from the automated overall category-stat feed (#182), so ROS
-- valuation is no longer anchored to hardcoded 2025 filenames and ratio
-- constants in each downstream model.
--
-- Method (The Process 2024, pp. 71--75):
-- * SGP factor = stat units per standings point, via SLOPE(value on points)
--   over mid-table teams (drop top/bottom 2 in 12-team leagues).
-- * Counting stats annualize: the current-season slope is divided by the
--   season-completion fraction before blending.
-- * Ratio stats (AVG/ERA/WHIP) are NOT annualized; their
--   numerator/denominator team context is retained and blended instead, and
--   downstream models keep the ((ctx + player) recomputed vs context) shape.
-- * Blend weight on the current season is linear in completion, so early
--   season stays anchored to completed-season data.
-- * OC and 50s calibrate separately. ME has no automated current feed and
--   keeps the historical leg only (is_fallback).
--
-- Historical leg: 2025 overall-standings slopes from mart_sgp_factors plus the
-- ratio contexts transcribed from the pre-#184 ROS SGP models (themselves
-- derived from 2025 standings as league-average 13-hitter / 8-pitcher shares:
-- e.g. OC 1765 H / 6958 AB implies a ~7494 AB full-team average at .2536).
-- Current leg: same OLS shape applied to the latest in-season snapshot,
-- with current contexts as league-average 13/14-hitter and 8/9-pitcher shares.

with calendar as (
    select
        cast(season_year as int) as season_year,
        cast(scoring_periods as int) as season_scoring_periods,
        cast(season_start_date as date) as season_start_date
    from {{ ref('season_scoring_calendar') }}
),

formats as (
    select 'oc' as format
    union all select 'me'
    union all select '50s'
),

-- Single documented home for the 2025 historical leg (was: one hardcoded
-- `_filename` plus inline ratio constants in each of six ROS SGP models).
hist_files as (
    select
        'oc' as format,
        'NFBC OC 2025 Overall Standings.csv' as hist_source_file,
        cast(1765.0 as double) as hist_avg_num,
        cast(6958.0 as double) as hist_avg_den,
        cast(0.2536 as double) as hist_avg_rate,
        cast(487.0 as double) as hist_era_num,
        cast(1163.0 as double) as hist_era_den,
        cast(3.7707 as double) as hist_era_rate,
        cast(1398.0 as double) as hist_whip_num,
        cast(1163.0 as double) as hist_whip_den,
        cast(1.2022 as double) as hist_whip_rate
    union all select
        'me',
        'NFBC ME 2025 Overall Standings.csv',
        1712.0, 6803.0, 0.2517,
        499.0, 1155.0, 3.885,
        1415.0, 1155.0, 1.223
    union all select
        '50s',
        'NFBC 50s 2025 Overall Standings.csv',
        1725.0, 6805.0, 0.2535,
        474.0, 1131.0, 3.774,
        1359.0, 1131.0, 1.201
),

hist_slopes as (
    select f.format, s.category, s.hist_slope, f.hist_source_file
    from hist_files f
    inner join (
        select
            _filename,
            category,
            hist_slope
        from (
            select _filename, 'R' as category, max(sgp_r) as hist_slope from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'HR', max(sgp_hr) from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'RBI', max(sgp_rbi) from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'SB', max(sgp_sb) from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'AVG', max(sgp_avg) from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'K', max(sgp_k) from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'W', max(sgp_w) from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'S', max(sgp_s) from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'ERA', max(sgp_era) from {{ ref('mart_sgp_factors') }} group by _filename
            union all select _filename, 'WHIP', max(sgp_whip) from {{ ref('mart_sgp_factors') }} group by _filename
        )
    ) s
        on s._filename = f.hist_source_file
),

latest as (
    select
        s.format,
        s.nfbc_league_id,
        coalesce(s.owner, s.team, cast(s.standing_rank as varchar)) as team_key,
        cast(s.r as double) as r,
        cast(s.hr as double) as hr,
        cast(s.rbi as double) as rbi,
        cast(s.sb as double) as sb,
        cast(s.avg as double) as avg,
        cast(s.k as double) as k,
        cast(s.w as double) as w,
        cast(s.sv as double) as sv,
        cast(s.era as double) as era,
        cast(s.whip as double) as whip,
        cast(s.h as double) as h,
        cast(s.ab as double) as ab,
        cast(s.er as double) as er,
        cast(s.ip as double) as ip,
        cast(s.ha as double) as ha,
        cast(s.bb as double) as bb,
        s.snapshot_date
    from {{ ref('stg_nfbc_in_season_overall_category_stats') }} s
    where s.is_latest_snapshot
        and s.format in ('oc', '50s')
),

-- Long category grain; order key ascends so ERA/WHIP rank low-to-high.
long_stats as (
    select format, nfbc_league_id, team_key, snapshot_date, category, value, num, den
    from (
        select format, nfbc_league_id, team_key, snapshot_date, 'R' as category, r as value, cast(null as double) as num, cast(null as double) as den from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'HR', hr, null, null from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'RBI', rbi, null, null from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'SB', sb, null, null from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'AVG', avg, h, ab from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'K', k, null, null from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'W', w, null, null from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'S', sv, null, null from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'ERA', era, er, ip from latest
        union all select format, nfbc_league_id, team_key, snapshot_date, 'WHIP', whip, ha + bb, ip from latest
    )
),

ranked as (
    select
        format,
        nfbc_league_id,
        team_key,
        snapshot_date,
        category,
        value,
        row_number() over (
            partition by format, nfbc_league_id, category
            order by
                case when category in ('ERA', 'WHIP') then value else -value end asc
        ) as cat_rank
    from long_stats
),

-- 12-team points mapping with the book's mid-table filter (ranks 3--10),
-- matching stg_nfbc_sgp_inputs for non-ME/DC files.
pointed as (
    select
        format,
        category,
        snapshot_date,
        13 - cat_rank as points,
        value
    from ranked
    where cat_rank between 3 and 10
),

current_slopes_raw as (
    select
        format,
        category,
        max(snapshot_date) as snapshot_date,
        (
            (count(*) * sum(points * value)) - (sum(points) * sum(value))
        )
        /
        nullif(
            (count(*) * sum(points * points)) - (sum(points) * sum(points)),
            0
        ) as slope_raw
    from pointed
    group by format, category
),

current_context as (
    select
        format,
        max(snapshot_date) as snapshot_date,
        -- League-average 13-hitter shares (14 lineup slots minus one).
        avg(h) * 13.0 / 14.0 as ctx_avg_num,
        avg(ab) * 13.0 / 14.0 as ctx_avg_den,
        sum(h) / nullif(sum(ab), 0) as ctx_avg_rate,
        -- League-average 8-pitcher shares (9 P slots minus one).
        avg(er) * 8.0 / 9.0 as ctx_era_num,
        avg(ip) * 8.0 / 9.0 as ctx_era_den,
        9.0 * sum(er) / nullif(sum(ip), 0) as ctx_era_rate,
        avg(ha + bb) * 8.0 / 9.0 as ctx_whip_num,
        avg(ip) * 8.0 / 9.0 as ctx_whip_den,
        sum(ha + bb) / nullif(sum(ip), 0) as ctx_whip_rate
    from latest
    group by format
),

blended as (
    select
        f.format,
        cat.category,
        cat.is_ratio,
        h.hist_source_file,
        h.hist_slope,
        s.slope_raw as current_slope_raw,
        case
            when cat.is_ratio then s.slope_raw
            else s.slope_raw / nullif(comp.season_completion, 0)
        end as current_slope,
        comp.snapshot_date,
        comp.periods_elapsed,
        comp.season_scoring_periods,
        comp.periods_elapsed * 1.0 / comp.season_scoring_periods as season_completion,
        case
            when s.slope_raw is null then 0.0
            else least(greatest(comp.season_completion, 0.0), 1.0)
        end as current_weight,
        hf.hist_avg_num as hist_avg_num,
        hf.hist_avg_den as hist_avg_den,
        hf.hist_avg_rate as hist_avg_rate,
        hf.hist_era_num as hist_era_num,
        hf.hist_era_den as hist_era_den,
        hf.hist_era_rate as hist_era_rate,
        hf.hist_whip_num as hist_whip_num,
        hf.hist_whip_den as hist_whip_den,
        hf.hist_whip_rate as hist_whip_rate,
        case cat.category
            when 'AVG' then cc.ctx_avg_num
            when 'ERA' then cc.ctx_era_num
            when 'WHIP' then cc.ctx_whip_num
        end as current_ctx_num,
        case cat.category
            when 'AVG' then cc.ctx_avg_den
            when 'ERA' then cc.ctx_era_den
            when 'WHIP' then cc.ctx_whip_den
        end as current_ctx_den,
        case cat.category
            when 'AVG' then cc.ctx_avg_rate
            when 'ERA' then cc.ctx_era_rate
            when 'WHIP' then cc.ctx_whip_rate
        end as current_ctx_rate
    from formats f
    cross join (
        select 'R' as category, false as is_ratio
        union all select 'HR', false
        union all select 'RBI', false
        union all select 'SB', false
        union all select 'AVG', true
        union all select 'K', false
        union all select 'W', false
        union all select 'S', false
        union all select 'ERA', true
        union all select 'WHIP', true
    ) cat
    left join hist_slopes h
        on h.format = f.format and h.category = cat.category
    left join current_slopes_raw s
        on s.format = f.format and s.category = cat.category
    left join current_context cc
        on cc.format = f.format
    left join hist_files hf
        on hf.format = f.format
    left join (
        select
            format,
            snapshot_date,
            periods_elapsed,
            season_scoring_periods,
            periods_elapsed * 1.0 / season_scoring_periods as season_completion
        from (
            select
                format,
                snapshot_date,
                least(
                    greatest(
                        date_diff(
                            'week',
                            (select season_start_date from calendar),
                            snapshot_date
                        ) + 1,
                        1
                    ),
                    (select season_scoring_periods from calendar)
                ) as periods_elapsed,
                (select season_scoring_periods from calendar) as season_scoring_periods
            from (
                select format, max(snapshot_date) as snapshot_date
                from latest
                group by format
            )
        )
    ) comp
        on comp.format = f.format
)

select
    format,
    category,
    is_ratio,
    hist_source_file,
    hist_slope,
    current_slope_raw,
    current_slope,
    snapshot_date,
    coalesce(periods_elapsed, 1) as scoring_periods_elapsed,
    coalesce(
        season_scoring_periods,
        (select season_scoring_periods from calendar)
    ) as season_scoring_periods,
    coalesce(season_completion, 0.0) as season_completion,
    current_weight,
    1.0 - current_weight as hist_weight,
    case
        when current_slope is null or hist_slope is null then coalesce(hist_slope, current_slope)
        else current_weight * current_slope + (1.0 - current_weight) * hist_slope
    end as final_slope,
    case when is_ratio then case category when 'AVG' then hist_avg_num when 'ERA' then hist_era_num when 'WHIP' then hist_whip_num end end as hist_ctx_num,
    case when is_ratio then case category when 'AVG' then hist_avg_den when 'ERA' then hist_era_den when 'WHIP' then hist_whip_den end end as hist_ctx_den,
    case when is_ratio then case category when 'AVG' then hist_avg_rate when 'ERA' then hist_era_rate when 'WHIP' then hist_whip_rate end end as hist_ctx_rate,
    current_ctx_num,
    current_ctx_den,
    current_ctx_rate,
    case
        when is_ratio and current_ctx_num is null
        then case category when 'AVG' then hist_avg_num when 'ERA' then hist_era_num when 'WHIP' then hist_whip_num end
        when is_ratio then
            current_weight * current_ctx_num + (1.0 - current_weight) * case category when 'AVG' then hist_avg_num when 'ERA' then hist_era_num when 'WHIP' then hist_whip_num end
    end as final_ctx_num,
    case
        when is_ratio and current_ctx_den is null
        then case category when 'AVG' then hist_avg_den when 'ERA' then hist_era_den when 'WHIP' then hist_whip_den end
        when is_ratio then
            current_weight * current_ctx_den + (1.0 - current_weight) * case category when 'AVG' then hist_avg_den when 'ERA' then hist_era_den when 'WHIP' then hist_whip_den end
    end as final_ctx_den,
    case
        when is_ratio and current_ctx_rate is null
        then case category when 'AVG' then hist_avg_rate when 'ERA' then hist_era_rate when 'WHIP' then hist_whip_rate end
        when is_ratio then
            current_weight * current_ctx_rate + (1.0 - current_weight) * case category when 'AVG' then hist_avg_rate when 'ERA' then hist_era_rate when 'WHIP' then hist_whip_rate end
    end as final_ctx_rate,
    (current_slope is null) as is_fallback
from blended
