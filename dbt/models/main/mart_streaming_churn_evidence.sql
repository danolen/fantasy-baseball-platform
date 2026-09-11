{{
    config(
        materialized='table'
    )
}}

-- Streaming churn evidence: observed weekly adds/drops per team (#185).
--
-- Grain: one row per format. An add is a player on this week's post-FAAB
-- roster who was absent from the same owner's roster the prior scoring
-- week (drops are symmetric). Team-weeks without a prior-week snapshot
-- for their league are skipped, not zero-filled. 50s leagues are excluded:
-- no FAAB means no streaming decisions.

with membership as (
    select
        m.league,
        m.owner,
        m.nfbc_id,
        m.week_key,
        lc.format
    from {{ ref('stg_nfbc_roster_membership') }} m
    inner join {{ ref('league_config') }} lc
        on lc.league = m.league
    where lc.format in ('oc', 'me')
),

league_weeks as (
    select distinct league, week_key
    from membership
),

-- Team-weeks whose prior scoring week has a snapshot for their league.
measurable as (
    select
        m.league,
        m.owner,
        m.week_key,
        max(lc.format) as format
    from membership m
    inner join {{ ref('league_config') }} lc
        on lc.league = m.league
    inner join league_weeks pw
        on pw.league = m.league
        and pw.week_key = m.week_key - interval '7' day
    group by m.league, m.owner, m.week_key
),

adds as (
    select
        t.league,
        t.owner,
        t.week_key,
        t.format,
        count(*) as roster_size,
        count(case when prev.nfbc_id is null then 1 end) as n_adds
    from measurable t
    inner join membership m
        on m.league = t.league
        and m.owner = t.owner
        and m.week_key = t.week_key
    left join membership prev
        on prev.league = t.league
        and prev.owner = t.owner
        and prev.nfbc_id = m.nfbc_id
        and prev.week_key = t.week_key - interval '7' day
    group by t.league, t.owner, t.week_key, t.format
),

drops as (
    select
        t.league,
        t.owner,
        t.week_key,
        count(case when curr.nfbc_id is null then 1 end) as n_drops
    from measurable t
    inner join membership prior
        on prior.league = t.league
        and prior.owner = t.owner
        and prior.week_key = t.week_key - interval '7' day
    left join membership curr
        on curr.league = t.league
        and curr.owner = t.owner
        and curr.nfbc_id = prior.nfbc_id
        and curr.week_key = t.week_key
    group by t.league, t.owner, t.week_key
),

team_weeks as (
    select
        a.league,
        a.owner,
        a.week_key,
        a.format,
        a.roster_size,
        a.n_adds,
        coalesce(d.n_drops, 0) as n_drops
    from adds a
    left join drops d
        on d.league = a.league
        and d.owner = a.owner
        and d.week_key = a.week_key
)

select
    format,
    count(*) as n_team_weeks,
    count(distinct league) as n_leagues,
    min(week_key) as first_week,
    max(week_key) as last_week,
    avg(roster_size * 1.0) as avg_roster_size,
    avg(n_adds * 1.0) as avg_adds,
    approx_percentile(n_adds, 0.25) as adds_p25,
    approx_percentile(n_adds, 0.50) as adds_p50,
    approx_percentile(n_adds, 0.75) as adds_p75,
    approx_percentile(n_adds, 0.90) as adds_p90,
    avg(n_drops * 1.0) as avg_drops,
    approx_percentile(n_drops, 0.25) as drops_p25,
    approx_percentile(n_drops, 0.50) as drops_p50,
    approx_percentile(n_drops, 0.75) as drops_p75,
    approx_percentile(n_drops, 0.90) as drops_p90,
    count(*) >= 4 as meets_history_minimum
from team_weeks
group by format
