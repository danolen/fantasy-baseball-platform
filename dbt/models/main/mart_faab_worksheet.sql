{{
    config(
        materialized='table'
    )
}}

with league_config as (
    select
        league,
        cast(ftn_league_size as int) as ftn_league_size,
        format
    from {{ ref('league_config') }}
),

weekly as (
    select wp.*, lc.format
    from {{ ref('mart_weekly_projections') }} wp
    inner join league_config lc
        on wp.league = lc.league
),

ftn_by_league as (
    select
        ftn.nfbc_id,
        ftn.player_clean,
        ftn.position as ftn_position,
        ftn.team as ftn_team,
        ftn.type as ftn_type,
        ftn.low_bid,
        ftn.high_bid,
        ftn.notes as ftn_notes,
        ftn.bid_change,
        ftn.status_tag,
        lc.league
    from {{ ref('stg_ftn_faab') }} ftn
    inner join league_config lc
        on ftn.league_size = lc.ftn_league_size
)

select
    coalesce(wp.id, ftn.nfbc_id) as nfbc_id,
    coalesce(concat(wp.first_name, ' ', wp.last_name), ftn.player_clean) as player,
    coalesce(wp.pos, ftn.ftn_position) as position,
    coalesce(wp.team, ftn.ftn_team) as team,
    coalesce(wp.league, ftn.league) as league,
    wp.owner,
    wp.own_pct,
    wp.week_of,
    wp.opps,
    wp.next_proj_opps,
    wp.num_g,
    wp.bats,
    wp.home_games,
    wp.away_games,
    wp.vs_rhp,
    wp.vs_lhp,
    wp.dollars,
    wp.dollars_per_game,
    wp.dollars_monday_thursday,
    wp.dollars_friday_sunday,
    wp.roster_pct,
    wp.ros12_dollars_per_game,
    wp.rfs12,
    wp.rfs15,
    case wp.format
        when 'oc' then wp.ros_oc
        when 'me' then wp.ros_me
    end as ros_value,
    ftn.ftn_type,
    ftn.low_bid,
    ftn.high_bid,
    ftn.ftn_notes,
    ftn.bid_change,
    ftn.status_tag,
    case when ftn.nfbc_id is not null then 1 else 0 end as has_ftn_rec
from weekly wp
full outer join ftn_by_league ftn
    on wp.id = ftn.nfbc_id
    and wp.league = ftn.league
