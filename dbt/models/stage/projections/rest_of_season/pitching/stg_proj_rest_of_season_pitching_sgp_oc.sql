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
        agg.ip,
        agg.er,
        agg.h,
        agg.bb,
        agg.w,
        --agg.qs,
        agg.k,
        agg.sv,
        agg.era,
        agg.whip,
        agg.k_per_9,
        agg.bb_per_9
    from {{ ref('stg_proj_rest_of_season_pitching_agg') }} agg
    inner join {{ ref('stg_mpd_player_id_map') }} ids
        on agg.id = ids.id
),

sgp_constants as (
    select sgp_k,
        sgp_w,
        sgp_s,
        sgp_era,
        sgp_whip
    from {{ ref('mart_sgp_factors') }}
    where _filename = 'NFBC OC 2025 Overall Standings.csv'
),

sgps as (
    select b.*,
        case when b.sv > 0 then 'RP'
            else 'SP' end as position,
        b.k/s.sgp_k as k_sgp,
        b.w/s.sgp_w as w_sgp,
        b.sv/s.sgp_s as sv_sgp,
        (((487 + er) * 9) / (1163 + ip) - 3.7707) / s.sgp_era as era_sgp,
        ((1398 + h + bb) / (1163 + ip) - 1.2022) / s.sgp_whip as whip_sgp
    from base b
    cross join sgp_constants s
)

select *,
    k_sgp + w_sgp + sv_sgp + era_sgp + whip_sgp as sgp
from sgps
