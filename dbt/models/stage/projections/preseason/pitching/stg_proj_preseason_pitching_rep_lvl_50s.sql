{{
    config(
        materialized='table'
    )
}}

with base as (
    select *,
        row_number() over (partition by position order by sgp desc) as pos_rank,
        case when position = 'SP' and row_number() over (partition by position order by sgp desc) <= 12*6 then 'Y'
            when position = 'RP' and row_number() over (partition by position order by sgp desc) <= 12*2 then 'Y'
            else 'N' end as include_in_pool
    from {{ ref('stg_proj_preseason_pitching_sgp_50s') }}
),

remaining_p as (
    select *,
        case when row_number() over (order by sgp desc) <= 12 then 'Y'
            else 'N' end as include_in_pool_p
    from base
    where include_in_pool = 'N'
),

draftable_pool as (
    select id,
        name,
        position,
        sgp
    from base
    where include_in_pool = 'Y'

    union all 

    select id,
        name,
        position,
        sgp
    from remaining_p
    where include_in_pool_p = 'Y'
)

select position,
    min(sgp) as replvl
from draftable_pool
group by 1