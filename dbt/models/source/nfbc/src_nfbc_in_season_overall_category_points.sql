{{
    config(
        materialized='table'
    )
}}

-- Raw NFBC overall category-points snapshots. History is retained; latest
-- selection happens in staging (#182). Snapshot fields come from Athena "$path".
select {{ dbt_utils.star(source('nfbc', 'in_season_overall_category_points')) }},
    regexp_extract("$path", 'year=([0-9]{4})', 1) as year,
    regexp_extract("$path", 'month=([0-9]{1,2})', 1) as month,
    regexp_extract("$path", 'day=([0-9]{1,2})', 1) as day,
    concat(
        regexp_extract("$path", 'year=([0-9]{4})', 1),
        lpad(regexp_extract("$path", 'month=([0-9]{1,2})', 1), 2, '0'),
        lpad(regexp_extract("$path", 'day=([0-9]{1,2})', 1), 2, '0')
    ) as _ptkey,
    element_at(split("$path", '/'), -1) as _filename,
    current_timestamp as _loaddatetime
from {{ source('nfbc', 'in_season_overall_category_points') }}
