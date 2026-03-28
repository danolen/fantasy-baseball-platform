{{
    config(
        materialized='table'
    )
}}

select pt.id,
    pt.pa,
    skills.ab * pt.pa as ab,
    skills.h * pt.pa as h,
    skills.x1b * pt.pa as x1b,
    skills.x2b * pt.pa as x2b,
    skills.x3b * pt.pa as x3b,
    skills.r * pt.pa as r,
    skills.hr * pt.pa as hr,
    skills.rbi * pt.pa as rbi,
    skills.sb * pt.pa as sb,
    skills.bb * pt.pa as bb,
    skills.hbp * pt.pa as hbp,
    skills.avg as avg,
    skills.obp as obp,
    skills.slg as slg
from {{ ref('stg_proj_preseason_hitting_pt') }} pt
inner join {{ ref('stg_proj_preseason_hitting_skills') }} skills
    on pt.id = skills.id