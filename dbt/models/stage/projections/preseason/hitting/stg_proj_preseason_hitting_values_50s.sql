{{
    config(
        materialized='table'
    )
}}

with base as (
    select sgp.*,
        replvl.replvl,
        sgp.sgp - replvl.replvl as sgpar
    from {{ ref('stg_proj_preseason_hitting_sgp_50s') }} sgp
    left join {{ ref('stg_proj_preseason_hitting_rep_lvl_50s') }} replvl
        on sgp.position = replvl.position
),

dollars as (
    select ((12 * 260 * 0.67) - (12 * 14)) / sum(sgpar) as dollars_per_sgp
    from base
    where sgpar > 0
)

select b.*,
    (b.sgpar * d.dollars_per_sgp) + 1 as value
from base b
cross join dollars d
order by value desc
