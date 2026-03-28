{{
    config(
        materialized='table'
    )
}}

select 
    rank,
    team,
    owners,
    league,
    cast(points as double) as points,
    cast(r as int) as r,
    cast(hr as int) as hr,
    cast(rbi as int) as rbi,
    cast(sb as int) as sb,
    cast(ab as int) as ab,
    cast(h as int) as h,
    cast(k as int) as k,
    cast(w as int) as w,
    cast(s as int) as s,
    cast(ip as double) as ip,
    cast(er as int) as er,
    cast(bb as int) as bb,
    cast(ha as int) as ha,
    cast(avg as double) as avg,
    cast(era as double) as era,
    cast(whip as double) as whip,
    _ptkey,
    _filename,
    _loaddatetime,
    row_number() over (partition by _filename, league order by cast(r as int) desc)     as rank_r,
    row_number() over (partition by _filename, league order by cast(hr as int) desc)    as rank_hr,
    row_number() over (partition by _filename, league order by cast(rbi as int) desc)   as rank_rbi,
    row_number() over (partition by _filename, league order by cast(sb as int) desc)    as rank_sb,
    row_number() over (partition by _filename, league order by cast(avg as double) desc)   as rank_avg,
    row_number() over (partition by _filename, league order by cast(k as int) desc)     as rank_k,
    row_number() over (partition by _filename, league order by cast(w as int) desc)     as rank_w,
    row_number() over (partition by _filename, league order by cast(s as int) desc)     as rank_s,
    row_number() over (partition by _filename, league order by cast(era as double) asc)    as rank_era,
    row_number() over (partition by _filename, league order by cast(whip as double) asc)   as rank_whip
from {{ ref('src_nfbc_standings') }}