{{
    config(
        materialized='table'
    )
}}

with ips as (
    select id,
        ip,
        proj_system
    from {{ ref('stg_fg_proj_preseason_pitching_per_ip') }}

    union all

    select id,
        ip,
        proj_system
    from {{ ref('stg_razzball_proj_preseason_pitching_per_ip') }}

    union all

    select id,
        ip,
        proj_system
    from {{ ref('stg_ftn_proj_preseason_pitching_per_ip') }}
)

select id,
    avg(ip) as ip
from ips
where proj_system in ('depthcharts','atc','thebat','razzball','ftn')
group by id