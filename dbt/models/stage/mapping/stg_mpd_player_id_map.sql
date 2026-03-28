{{
    config(
        materialized='table'
    )
}}

select distinct 
    players.id,
    concat(element_at(SPLIT(players.players, ', '), 2), ' ', element_at(SPLIT(players.players, ', '), 1)) as name,
    players.team,
    players.pos,
    id.mlbid,
    id.idfangraphs,
    id.underdog,
    id.razzballid,
    id.bpid
from {{ ref('src_nfbc_players') }} players
left join {{ ref('src_mpd_player_id_map') }} id
    on players.id = id.nfbcid
where id.mlbid != ''
    or id.idfangraphs != ''
    or id.underdog != ''
    or id.razzballid != ''
    or id.bpid != ''