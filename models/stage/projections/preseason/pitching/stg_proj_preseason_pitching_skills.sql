{{
    config(
        materialized='table'
    )
}}

select id,
    avg(er) as er,
    avg(h) as h,
    avg(bb) as bb,
    avg(w) as w,
    --avg(qs) as qs,
    avg(k) as k,
    avg(
      case
        when proj_system != 'thebat' then sv
        else null
      end
    ) as sv,
    avg(era) as era,
    avg(whip) as whip,
    avg(k_per_9) as k_per_9,
    avg(bb_per_9) as bb_per_9
from (
    select *
    from {{ ref('stg_fg_proj_preseason_pitching_per_ip') }}
    
    union all
    
    select *
    from {{ ref('stg_ftn_proj_preseason_pitching_per_ip') }})
where proj_system in ('zips','steamer','atc','thebat','oopsy','ftn')
group by id