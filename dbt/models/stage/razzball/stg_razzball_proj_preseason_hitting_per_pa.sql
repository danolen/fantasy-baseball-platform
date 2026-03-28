{{
    config(
        materialized='table'
    )
}}

select ids.id,
    ids.name,
    ids.team,
    replace(_filename, '-hit.csv', '') as proj_system,
    cast(proj.pa as double) as pa,
    cast(proj.ab as double)/cast(proj.pa as double) as ab,
    cast(proj.h as double)/cast(proj.pa as double) as h,
    cast(proj.x1b as double)/cast(proj.pa as double) as x1b,
    cast(proj.x2b as double)/cast(proj.pa as double) as x2b,
    cast(proj.x3b as double)/cast(proj.pa as double) as x3b,
    cast(proj.r as double)/cast(proj.pa as double) as r,
    cast(proj.hr as double)/cast(proj.pa as double) as hr,
    cast(proj.rbi as double)/cast(proj.pa as double) as rbi,
    cast(proj.sb as double)/cast(proj.pa as double) as sb,
    cast(proj.avg as double) as avg,
    cast(proj.obp as double) as obp,
    cast(proj.slg as double) as slg,
    cast(proj.bb as double)/cast(proj.pa as double) as bb,
    cast(proj.hbp as double)/cast(proj.pa as double) as hbp
from {{ ref('src_razzball_projections_preseason_hitting') }} proj
inner join {{ ref('stg_mpd_player_id_map') }} ids
    on proj.razzid = ids.razzballid