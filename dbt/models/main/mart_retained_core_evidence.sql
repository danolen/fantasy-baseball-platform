{{
    config(
        materialized='table'
    )
}}

-- Retained-core evidence: observed 2- and 3-week roster retention (#185).
--
-- Grain: one row per (format, row_type, pos_group, ros_band). Each rostered
-- (league, owner, player, week) cohort is checked for the same player on the
-- same owner's roster 2 and 3 scoring weeks later. Retention here means
-- still owned, not started: expected starts come from the lineup optimizer.
-- ROS bands use current rest-of-season $ (historical ROS $ is not retained),
-- so bands describe who the player is now, not what was known then.
-- 50s leagues are excluded: no FAAB means no retain/drop decisions.

with membership as (
    select
        m.league,
        m.owner,
        m.nfbc_id,
        m.row_type,
        m.pos_group,
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

ros_values as (
    select cast(id as varchar) as nfbc_id, 'oc' as format, max(value) as ros_value
    from (
        select id, value from {{ ref('stg_proj_rest_of_season_hitting_values_oc') }}
        union all
        select id, value from {{ ref('stg_proj_rest_of_season_pitching_values_oc') }}
    )
    group by id
    union all
    select cast(id as varchar) as nfbc_id, 'me' as format, max(value) as ros_value
    from (
        select id, value from {{ ref('stg_proj_rest_of_season_hitting_values_me') }}
        union all
        select id, value from {{ ref('stg_proj_rest_of_season_pitching_values_me') }}
    )
    group by id
),

cohorts as (
    select
        m.league,
        m.owner,
        m.nfbc_id,
        m.row_type,
        m.pos_group,
        m.format,
        m.week_key,
        case
            when v.ros_value is null then 'unranked'
            when v.ros_value <= 1 then '<=1'
            when v.ros_value <= 3 then '1-3'
            when v.ros_value <= 5 then '3-5'
            else '>5'
        end as ros_band,
        case when w2.week_key is not null then 1 else 0 end as w2_observable,
        case
            when w2.week_key is not null and m2.nfbc_id is not null then 1
            else 0
        end as retained_w2,
        case when w3.week_key is not null then 1 else 0 end as w3_observable,
        case
            when w3.week_key is not null and m3.nfbc_id is not null then 1
            else 0
        end as retained_w3
    from membership m
    left join ros_values v
        on v.nfbc_id = m.nfbc_id
        and v.format = m.format
    left join league_weeks w2
        on w2.league = m.league
        and w2.week_key = m.week_key + interval '14' day
    left join membership m2
        on m2.league = m.league
        and m2.owner = m.owner
        and m2.nfbc_id = m.nfbc_id
        and m2.week_key = m.week_key + interval '14' day
    left join league_weeks w3
        on w3.league = m.league
        and w3.week_key = m.week_key + interval '21' day
    left join membership m3
        on m3.league = m.league
        and m3.owner = m.owner
        and m3.nfbc_id = m.nfbc_id
        and m3.week_key = m.week_key + interval '21' day
)

select
    format,
    row_type,
    pos_group,
    ros_band,
    count(*) as n_rostered,
    sum(w2_observable) as n_w2_observable,
    case
        when sum(w2_observable) = 0 then null
        else sum(retained_w2) * 1.0 / sum(w2_observable)
    end as retention_w2,
    sum(w3_observable) as n_w3_observable,
    case
        when sum(w3_observable) = 0 then null
        else sum(retained_w3) * 1.0 / sum(w3_observable)
    end as retention_w3,
    count(distinct week_key) as cohort_weeks,
    min(week_key) as first_cohort_week,
    max(week_key) as last_cohort_week,
    count(distinct week_key) >= 4 as meets_history_minimum
from cohorts
group by format, row_type, pos_group, ros_band
