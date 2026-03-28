{{
    config(
        materialized='table'
    )
}}

select ids.id,
    ids.name,
    ids.team,
    replace(_filename, '-pitch.csv', '') as proj_system,
    cast(proj.ip as double) as ip,
    cast(proj.er as double)/cast(proj.ip as double) as er,
    cast(proj.h as double)/cast(proj.ip as double) as h,
    cast(proj.bb as double)/cast(proj.ip as double) as bb,
    cast(proj.w as double)/cast(proj.ip as double) as w,
    cast(proj.qs as double)/cast(proj.ip as double) as qs,
    cast(proj.k as double)/cast(proj.ip as double) as k,
    cast(proj.sv as double)/cast(proj.ip as double) as sv,
    cast(proj.era as double) as era,
    cast(proj.whip as double) as whip,
    (cast(proj.k as double)/cast(proj.ip as double))*9 as k_per_9,
    (cast(proj.bb as double)/cast(proj.ip as double))*9 as bb_per_9
from {{ ref('src_razzball_projections_preseason_pitching') }} proj
inner join {{ ref('stg_mpd_player_id_map') }} ids
    on proj.razzid = ids.razzballid