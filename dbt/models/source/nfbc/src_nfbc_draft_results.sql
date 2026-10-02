{{
    config(
        materialized='table'
    )
}}

-- 2026 NFBC Main Event + Online Championship snake-draft pick logs (#299).
-- Latest ingest partition only; analysis marts and slot dollars are follow-ups.
select {{ dbt_utils.star(source('nfbc', 'draft_results')) }},
    regexp_extract("$path", 'year=([0-9]{4})', 1) as year,
    regexp_extract("$path", 'month=([0-9]{1,2})', 1) as month,
    regexp_extract("$path", 'day=([0-9]{1,2})', 1) as day,
    concat(regexp_extract("$path", 'year=([0-9]{4})', 1),
        regexp_extract("$path", 'month=([0-9]{1,2})', 1),
        regexp_extract("$path", 'day=([0-9]{1,2})', 1)) as _ptkey,
    element_at(SPLIT("$path", '/'), -1) as _filename,
    current_timestamp as _loaddatetime
from {{ source('nfbc', 'draft_results') }}
where concat(regexp_extract("$path", 'year=([0-9]{4})', 1),
    regexp_extract("$path", 'month=([0-9]{1,2})', 1),
    regexp_extract("$path", 'day=([0-9]{1,2})', 1)) = (select max(concat(regexp_extract("$path", 'year=([0-9]{4})', 1),
                                                        regexp_extract("$path", 'month=([0-9]{1,2})', 1),
                                                        regexp_extract("$path", 'day=([0-9]{1,2})', 1))) from {{ source('nfbc', 'draft_results') }})
