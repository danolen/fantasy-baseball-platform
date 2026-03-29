{{
    config(
        materialized='table'
    )
}}

select id,
    avg(pa) as pa
from {{ ref('stg_fg_proj_rest_of_season_hitting_per_pa') }}
where proj_system in ('depthcharts','atc','thebat-x')
group by id
