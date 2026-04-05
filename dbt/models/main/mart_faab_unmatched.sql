{{
    config(
        materialized='table'
    )
}}

select
    player_raw,
    player_clean,
    position,
    team,
    type,
    low_bid,
    high_bid,
    league_size
from {{ ref('stg_ftn_faab') }}
where nfbc_id is null
