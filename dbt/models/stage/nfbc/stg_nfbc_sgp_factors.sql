{{
    config(
        materialized='table'
    )
}}

select
    _filename,
    category,
    (
        (n * sum_xy) - (sum_x * sum_y)
    )
    /
    nullif(
        (n * sum_x2) - (sum_x * sum_x),
        0
    ) as sgp_value

from (
    select
        _filename,
        category,
        count(*) as n,
        sum(points) as sum_x,
        sum(value) as sum_y,
        sum(points * value) as sum_xy,
        sum(points * points) as sum_x2
    from {{ ref('stg_nfbc_sgp_inputs') }}
    group by _filename, category
)
