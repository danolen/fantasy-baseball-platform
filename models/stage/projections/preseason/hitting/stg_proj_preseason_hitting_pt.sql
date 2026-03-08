{{
    config(
        materialized='table'
    )
}}

with pas as (
    select id,
        pa,
        proj_system
    from {{ ref('stg_fg_proj_preseason_hitting_per_pa') }}

    union all

    select id,
        pa,
        proj_system
    from {{ ref('stg_razzball_proj_preseason_hitting_per_pa') }}

    union all

    select id,
        pa,
        proj_system
    from {{ ref('stg_ftn_proj_preseason_hitting_per_pa') }}
)

select id,
    avg(pa) as pa
from pas
where proj_system in ('depthcharts','atc','thebat-x','razzball','ftn')
group by id