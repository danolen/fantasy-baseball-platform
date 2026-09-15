{{
    config(
        materialized='table'
    )
}}

-- Free-agent replacement curve: available weekly $ and counting stats by
-- rank (#185 / #188).
--
-- Grain: one row per (format, row_type, fa_rank). Within each league's
-- latest weekly lineup inputs, free agents (empty owner) with a weekly
-- projection are ranked by dollars; the mart keeps the median $ and mean
-- counting stats across leagues per format so one thin/thick wire does not
-- set the curve.
--
-- Replacement production for #188 is read off this curve through the rank
-- windows in the assumptions seed (e.g. base = mean of ranks 5-10),
-- deliberately below the single best free agent per The Process pp. 251-252
-- (the available pool is much worse than managers assume).
-- Counting stats use mean, not independent medians, so H/AB and ER/IP stay
-- a coherent rate profile.

with latest_weeks as (
    select league, max(week_of) as week_of
    from {{ ref('mart_weekly_lineup_inputs') }}
    group by league
),

ranked_fa as (
    select
        li.format,
        li.row_type,
        li.dollars,
        li.r,
        li.hr,
        li.rbi,
        li.sb,
        li.hits,
        li.ab,
        li.k,
        li.w,
        li.sv,
        li.er,
        li.ip,
        li.hits_allowed,
        li.walks_allowed,
        row_number() over (
            partition by li.league, li.row_type
            order by li.dollars desc
        ) as fa_rank
    from {{ ref('mart_weekly_lineup_inputs') }} li
    inner join latest_weeks w
        on w.league = li.league
        and w.week_of = li.week_of
    inner join {{ ref('league_config') }} lc
        on lc.league = li.league
    where (li.owner is null or trim(li.owner) = '')
        and li.dollars is not null
        and lc.format in ('oc', 'me')
        and li.format in ('oc', 'me')
)

select
    format,
    row_type,
    fa_rank,
    approx_percentile(dollars, 0.50) as median_weekly_dollars,
    avg(dollars) as mean_weekly_dollars,
    count(*) as n_leagues,
    max(dollars) as max_weekly_dollars,
    min(dollars) as min_weekly_dollars,
    avg(r) as mean_weekly_r,
    avg(hr) as mean_weekly_hr,
    avg(rbi) as mean_weekly_rbi,
    avg(sb) as mean_weekly_sb,
    avg(hits) as mean_weekly_h,
    avg(ab) as mean_weekly_ab,
    avg(k) as mean_weekly_k,
    avg(w) as mean_weekly_w,
    avg(sv) as mean_weekly_sv,
    avg(er) as mean_weekly_er,
    avg(ip) as mean_weekly_ip,
    avg(hits_allowed) as mean_weekly_ha,
    avg(walks_allowed) as mean_weekly_bb
from ranked_fa
where fa_rank <= 30
group by format, row_type, fa_rank
