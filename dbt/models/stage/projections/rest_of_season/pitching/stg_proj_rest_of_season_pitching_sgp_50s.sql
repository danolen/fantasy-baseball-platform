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
    select
        max(case when category = 'K' then final_slope end) as sgp_k,
        max(case when category = 'W' then final_slope end) as sgp_w,
        max(case when category = 'S' then final_slope end) as sgp_s,
        max(case when category = 'ERA' then final_slope end) as sgp_era,
        max(case when category = 'ERA' then final_ctx_num end) as era_ctx_num,
        max(case when category = 'ERA' then final_ctx_den end) as era_ctx_den,
        max(case when category = 'ERA' then final_ctx_rate end) as era_ctx_rate,
        max(case when category = 'WHIP' then final_slope end) as sgp_whip,
        max(case when category = 'WHIP' then final_ctx_num end) as whip_ctx_num,
        max(case when category = 'WHIP' then final_ctx_den end) as whip_ctx_den,
        max(case when category = 'WHIP' then final_ctx_rate end) as whip_ctx_rate
    from {{ ref('mart_ros_sgp_calibration') }}
    where format = '50s'
),

sgps as (
    select b.*,
        case when b.sv > 0 then 'RP'
            else 'SP' end as position,
        b.k/s.sgp_k as k_sgp,
        b.w/s.sgp_w as w_sgp,
        b.sv/s.sgp_s as sv_sgp,
        (((s.era_ctx_num + er) * 9) / (s.era_ctx_den + ip) - s.era_ctx_rate) / s.sgp_era as era_sgp,
        ((s.whip_ctx_num + h + bb) / (s.whip_ctx_den + ip) - s.whip_ctx_rate) / s.sgp_whip as whip_sgp
    from base b
    cross join sgp_constants s
)

select *,
    k_sgp + w_sgp + sv_sgp + era_sgp + whip_sgp as sgp
from sgps
