{{
    config(
        materialized='table'
    )
}}

select {{ dbt_utils.star(source('razzball', 'projections_preseason_hitting')) }},
    regexp_extract("$path", 'year=([0-9]{4})', 1) as year,
    regexp_extract("$path", 'month=([0-9]{1,2})', 1) as month,
    regexp_extract("$path", 'day=([0-9]{1,2})', 1) as day,
    concat(regexp_extract("$path", 'year=([0-9]{4})', 1),
        regexp_extract("$path", 'month=([0-9]{1,2})', 1),
        regexp_extract("$path", 'day=([0-9]{1,2})', 1)) as _ptkey,
    element_at(SPLIT("$path", '/'), -1) as _filename,
    current_timestamp as _loaddatetime
from {{ source('razzball', 'projections_preseason_hitting') }}
where concat(regexp_extract("$path", 'year=([0-9]{4})', 1),
    regexp_extract("$path", 'month=([0-9]{1,2})', 1),
    regexp_extract("$path", 'day=([0-9]{1,2})', 1)) = (select max(concat(regexp_extract("$path", 'year=([0-9]{4})', 1),
                                                        regexp_extract("$path", 'month=([0-9]{1,2})', 1),
                                                        regexp_extract("$path", 'day=([0-9]{1,2})', 1))) from {{ source('razzball', 'projections_preseason_hitting') }})