{{
    config(
        materialized='table'
    )
}}

with base as (
    select distinct 
        ids.id,
        ids.name,
        ids.team,
        ids.pos,
        agg.pa,
        agg.ab,
        agg.h,
        agg.x1b,
        agg.x2b,
        agg.x3b,
        agg.r,
        agg.hr,
        agg.rbi,
        agg.sb,
        agg.bb,
        agg.hbp,
        agg.avg,
        agg.obp,
        agg.slg
    from {{ ref('stg_proj_preseason_hitting_agg') }} agg
    inner join {{ ref('stg_mpd_player_id_map') }} ids
        on agg.id = ids.id
),

sgp_constants as (
    select sgp_r,
        sgp_hr,
        sgp_rbi,
        sgp_sb,
        sgp_avg
    from {{ ref('mart_sgp_factors') }}
    where _filename = 'NFBC ME 2025 Overall Standings.csv'
),

sgps as (
    select b.*,
        case when pos like '%C%' then 'C'
            when pos like '%2B%' then '2B'
            when pos like '%3B%' then '3B'
            when pos like '%OF%' then 'OF'
            when pos like '%1B%' then '1B'
            when pos like '%SS%' then 'SS'
            else 'UT' end as position,
        case when pos like '%C%' then 'C'
            when pos like '%2B%' then 'MI'
            when pos like '%3B%' then 'CI'
            when pos like '%OF%' then 'OF'
            when pos like '%1B%' then 'CI'
            when pos like '%SS%' then 'MI'
            else 'UT' end as pos_group,
        b.r/s.sgp_r as r_sgp,
        b.hr/s.sgp_hr as hr_sgp,
        b.rbi/s.sgp_rbi as rbi_sgp,
        b.sb/s.sgp_sb as sb_sgp,
        ((h + 1712.0) / (ab + 6803.0) - 0.2517) / s.sgp_avg as avg_sgp
    from base b
    cross join sgp_constants s
)

select *,
    case when pos like '%UT%' then r_sgp + hr_sgp + rbi_sgp + sb_sgp + avg_sgp - 0.25
        when pos like '%,%' then r_sgp + hr_sgp + rbi_sgp + sb_sgp + avg_sgp + 0.25
        else r_sgp + hr_sgp + rbi_sgp + sb_sgp + avg_sgp end as sgp
from sgps