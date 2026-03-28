{{
    config(
        materialized='table'
    )
}}

select _filename,
    max(case when category = 'R'  then sgp_value end) as sgp_r,
    max(case when category = 'HR'  then sgp_value end) as sgp_hr,
    max(case when category = 'RBI' then sgp_value end) as sgp_rbi,
    max(case when category = 'SB'  then sgp_value end) as sgp_sb,
    max(case when category = 'AVG' then sgp_value end) as sgp_avg,
    max(case when category = 'K'  then sgp_value end) as sgp_k,
    max(case when category = 'W'  then sgp_value end) as sgp_w,
    max(case when category = 'S' then sgp_value end) as sgp_s,
    max(case when category = 'ERA'  then sgp_value end) as sgp_era,
    max(case when category = 'WHIP' then sgp_value end) as sgp_whip
from {{ ref('stg_nfbc_sgp_factors') }}
group by _filename
