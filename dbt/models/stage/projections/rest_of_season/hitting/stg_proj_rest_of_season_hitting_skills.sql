{{
    config(
        materialized='table'
    )
}}

select id,
    avg(ab) as ab,
    avg(h) as h,
    avg(x1b) as x1b,
    avg(x2b) as x2b,
    avg(x3b) as x3b,
    avg(r) as r,
    avg(hr) as hr,
    avg(rbi) as rbi,
    avg(sb) as sb,
    avg(bb) as bb,
    avg(hbp) as hbp,
    avg(avg) as avg,
    avg(obp) as obp,
    avg(slg) as slg
from {{ ref('stg_fg_proj_rest_of_season_hitting_per_pa') }}
where proj_system in ('zips','steamer','atc','thebat-x','oopsy')
group by id
