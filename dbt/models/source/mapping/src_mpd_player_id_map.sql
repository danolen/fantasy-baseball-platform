{{
    config(
        materialized='table'
    )
}}

select {{ dbt_utils.star(source('mapping', 'player_id_map')) }},
    current_timestamp as _loaddatetime
from {{ source('mapping', 'player_id_map') }}