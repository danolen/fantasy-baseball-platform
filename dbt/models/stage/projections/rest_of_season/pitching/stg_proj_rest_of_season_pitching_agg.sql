{{
    config(
        materialized='table'
    )
}}

select pt.id,
    pt.ip,
    skills.er * pt.ip as er,
    skills.h * pt.ip as h,
    skills.bb * pt.ip as bb,
    skills.w * pt.ip as w,
    --skills.qs * pt.ip as qs,
    skills.k * pt.ip as k,
    skills.sv * pt.ip as sv,
    skills.era as era,
    skills.whip as whip,
    skills.k_per_9 as k_per_9,
    skills.bb_per_9 as bb_per_9
from {{ ref('stg_proj_rest_of_season_pitching_pt') }} pt
inner join {{ ref('stg_proj_rest_of_season_pitching_skills') }} skills
    on pt.id = skills.id
where pt.id != '9810'
