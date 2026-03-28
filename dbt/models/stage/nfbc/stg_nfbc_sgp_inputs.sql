{{
    config(
        materialized='table'
    )
}}

with unpivoted as (
    select _filename, league, 'R' as category, rank_r as rank, r as value
    from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'HR',  rank_hr,  hr   from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'RBI', rank_rbi, rbi  from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'SB',  rank_sb,  sb   from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'AVG', rank_avg, avg  from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'K',   rank_k,   k    from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'W',   rank_w,   w    from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'S',   rank_s,   s    from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'ERA', rank_era, era  from {{ ref('stg_nfbc_ranked_standings') }}
    union all select _filename, league, 'WHIP',rank_whip,whip from {{ ref('stg_nfbc_ranked_standings') }}
),

filtered as (
    select *
    from unpivoted
    where ((_filename like 'NFBC ME%' or _filename like 'NFBC DC%')
        and rank between 4 and 12)
        or (_filename not like 'NFBC ME%' and _filename not like 'NFBC DC%'
        and rank between 3 and 10)
        
),

agg as (
    select
        _filename,
        category,
        rank,
        avg(value) as avgvalue
    from filtered
    group by _filename, category, rank
)

select
    _filename,
    category,
    rank,
    case 
        when _filename like 'NFBC ME%' or _filename like 'NFBC DC%' then 16 - rank
        else 13 - rank end as points,
    avgvalue as value
from agg