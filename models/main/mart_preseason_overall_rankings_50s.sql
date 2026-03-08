{{
    config(
        materialized='table'
    )
}}

with base as (
    select id,
        name,
        team,
        pos,
        position,
        pos_group,
        pa,
        ab,
        h,
        x1b,
        x2b,
        x3b,
        r,
        hr,
        rbi,
        sb,
        bb,
        hbp,
        avg,
        obp,
        slg,
        null as ip,
        null as er,
        null as w,
        --null as qs,
        null as k,
        null as sv,
        null as era,
        null as whip,
        null as k_per_9,
        r_sgp,
        hr_sgp,
        rbi_sgp,
        sb_sgp,
        avg_sgp,
        null as w_sgp,
        null as k_sgp,
        null as sv_sgp,
        null as era_sgp,
        null as whip_sgp,
        sgp,
        replvl,
        sgpar,
        value
    from {{ ref('stg_proj_preseason_hitting_values_50s') }}

    union all

    select id,
        name,
        team,
        pos,
        position,
        'P' as pos_group,
        null as pa,
        null as ab,
        h,
        null as x1b,
        null as x2b,
        null as x3b,
        null as r,
        null as hr,
        null as rbi,
        null as sb,
        bb,
        null as hbp,
        null as avg,
        null as obp,
        null as slg,
        ip,
        er,
        w,
        --qs,
        k,
        sv,
        era,
        whip,
        k_per_9,
        null as r_sgp,
        null as hr_sgp,
        null as rbi_sgp,
        null as sb_sgp,
        null as avg_sgp,
        w_sgp,
        k_sgp,
        sv_sgp,
        era_sgp,
        whip_sgp,
        sgp,
        replvl,
        sgpar,
        value
    from {{ ref('stg_proj_preseason_pitching_values_50s') }}
),

rosters as (
    select rost.*,
        ids.id
    from {{ ref('src_fangraphs_opening_day_rosters') }} rost
    inner join {{ ref('stg_mpd_player_id_map') }} ids
        on rost.playerid = ids.idfangraphs
    where concat(rost.playerid, rost.pos) != '19755SP'
)

select row_number() over (order by b.value desc) as rank,
    b.*,
    cast(adp.adp as double) as adp,
    cast(adp.min_pick as int) as min_pick,
    cast(adp.max_pick as int) as max_pick,
    cast(adp.adp as double) - row_number() over (order by b.value desc) as rank_diff,
    rost.projected_opening_day_status
from base b
left join {{ ref('src_nfbc_adp') }} adp
    on b.id = adp.playerid
    and adp._filename = 'Fifties_ADP.tsv'
left join rosters rost
    on b.id = rost.id
order by value desc
