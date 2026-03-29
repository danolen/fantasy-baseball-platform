{{
    config(
        materialized='table'
    )
}}

select id,
    avg(ip) as ip
from {{ ref('stg_fg_proj_rest_of_season_pitching_per_ip') }}
where proj_system in ('depthcharts','atc','thebat')
group by id
