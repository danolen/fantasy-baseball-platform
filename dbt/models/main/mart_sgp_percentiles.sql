{{
    config(
        materialized='table'
    )
}}

with base as (
    select _filename, 'R'   as category, cast(r   as double) as val from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'HR',  cast(hr  as double) from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'RBI', cast(rbi as double) from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'SB',  cast(sb  as double) from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'AVG', cast(avg as double) from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'K',   cast(k   as double) from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'W',   cast(w   as double) from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'S',   cast(s   as double) from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'ERA', cast(era as double) * -1 from {{ ref('src_nfbc_standings') }}
    union all select _filename, 'WHIP', cast(whip as double) * -1 from {{ ref('src_nfbc_standings') }}
)

select
    _filename,
    category,
    case
        when category in ('ERA', 'WHIP')
            then approx_percentile(val, 0.8) * -1
        else approx_percentile(val, 0.8)
    end as p80,
    case
        when category in ('ERA', 'WHIP')
            then approx_percentile(val, 0.9) * -1
        else approx_percentile(val, 0.9)
    end as p90
from base
group by _filename, category

