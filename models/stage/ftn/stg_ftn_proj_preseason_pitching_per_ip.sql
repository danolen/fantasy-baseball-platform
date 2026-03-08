{{
    config(
        materialized='table'
    )
}}

select ids.id,
    ids.name,
    ids.team,
    _filename as proj_system,
    cast(proj.ip as double) as ip,
    cast(proj.er as double)/cast(proj.ip as double) as er,
    cast(proj.ha as double)/cast(proj.ip as double) as h,
    cast(proj.bb_pitcher as double)/cast(proj.ip as double) as bb,
    cast(proj.w as double)/cast(proj.ip as double) as w,
    --cast(coalesce(proj.qs,'0') as double)/cast(proj.ip as double) as qs,
    cast(proj.k_pitcher as double)/cast(proj.ip as double) as k,
    cast(proj.sv as double)/cast(proj.ip as double) as sv,
    cast(proj.era as double) as era,
    cast(proj.whip as double) as whip,
    cast(proj.k_per_9 as double) as k_per_9,
    (cast(proj.bb_pitcher as double)/cast(proj.ip as double))*9 as bb_per_9
from {{ ref('src_ftn_projections_preseason') }} proj
inner join {{ ref('stg_mpd_player_id_map') }} ids
    on proj.nfbcid = ids.id
where (proj.position like '%SP%'
    or proj.position like '%RP%')
    and proj.position not like '%UT%'