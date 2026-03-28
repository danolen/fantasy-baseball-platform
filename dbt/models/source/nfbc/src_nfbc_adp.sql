{{ config(
    materialized = 'table'
) }}

with adp as (
    select {{ dbt_utils.star(source('nfbc', 'adp')) }},
        regexp_extract("$path", 'year=([0-9]{4})', 1) as year,
        regexp_extract("$path", 'month=([0-9]{1,2})', 1) as month,
        regexp_extract("$path", 'day=([0-9]{1,2})', 1) as day,
        concat(regexp_extract("$path", 'year=([0-9]{4})', 1),
            regexp_extract("$path", 'month=([0-9]{1,2})', 1),
            regexp_extract("$path", 'day=([0-9]{1,2})', 1)) as _ptkey,
        element_at(SPLIT("$path", '/'), -1) as _filename,
        current_timestamp as _loaddatetime,
        rank() over (partition by element_at(SPLIT("$path", '/'), -1) 
                    order by concat(regexp_extract("$path", 'year=([0-9]{4})', 1),
                        regexp_extract("$path", 'month=([0-9]{1,2})', 1),
                        regexp_extract("$path", 'day=([0-9]{1,2})', 1)) desc) as _rnk
    from {{ source('nfbc', 'adp') }}
)

select *
from adp
where _rnk = 1