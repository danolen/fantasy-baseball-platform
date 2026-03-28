{{
    config(
        materialized='table'
    )
}}

with base as (
    select *,
        row_number() over (partition by position order by sgp desc) as pos_rank,
        case when position in ('1B','2B','3B','SS') and row_number() over (partition by position order by sgp desc) <= 12 then 'Y'
            when position = 'OF' and row_number() over (partition by position order by sgp desc) <= 12*5 then 'Y'
            when position = 'C'  and row_number() over (partition by position order by sgp desc) <= 12*2 then 'Y'
            else 'N' end as include_in_pool
    from {{ ref('stg_proj_preseason_hitting_sgp_oc') }}
),

remaining_pos_group as (
    select *,
        case when pos_group in ('MI','CI') and row_number() over (partition by pos_group order by sgp desc) <= 12 then 'Y'
            else 'N' end as include_in_pool_mi_ci
    from base
    where include_in_pool = 'N'
),

remaining_util as (
    select *,
        case when row_number() over (order by sgp desc) <= 12 then 'Y'
            else 'N' end as include_in_pool_ut
    from remaining_pos_group
    where include_in_pool_mi_ci = 'N'
),

draftable_pool as (
    select id,
        name,
        position,
        sgp
    from base
    where include_in_pool = 'Y'

    union all 

    select id,
        name,
        position,
        sgp
    from remaining_pos_group
    where include_in_pool_mi_ci = 'Y'

    union all 

    select id,
        name,
        position,
        sgp
    from remaining_util
    where include_in_pool_ut = 'Y'
),

rep_lvl as (
    select position,
        min(sgp) as replvl
    from draftable_pool
    where position != 'UT'
    group by 1
)

select *
from rep_lvl
union all
select 'UT' as position, (select max(replvl) from rep_lvl) as replvl