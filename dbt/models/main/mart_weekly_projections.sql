{{
    config(
        materialized='table'
    )
}}

select 
    nfbc.id,
    split_part(nfbc.players, ', ', 2) first_name,
    split_part(nfbc.players, ', ', 1) last_name,
    nfbc.pos,
    nfbc.team,
    nfbc.owner,
    cast(nfbc.own_pct as int) own_pct,
    regexp_replace(nfbc._filename, '.csv', '') league,
    coalesce(hit.wk_of, pitch.week_of) week_of,
    coalesce(hit.opps, pitch.opp) opps,
    pitch.next_proj_opps,
    hit.b bats,
    cast(hit.num_g as int) num_g,
    cast(hit.hg as int) home_games,
    cast(hit.ag as int) away_games,
    cast(hit.vr as int) vs_rhp,
    cast(hit.vl as int) vs_lhp,
    cast(coalesce(hit.dollars, pitch.dollars) as double) dollars,
    cast(coalesce(hit.dollars_per_game, pitch.dollars_per_game) as double) dollars_per_game,
    cast(hit.dollars_monday_thursday as double) dollars_monday_thursday,
    cast(hit.dollars_friday_sunday as double) dollars_friday_sunday,
    cast(coalesce(hit.roster_pct, pitch.roster_pct) as int) roster_pct,
    cast(coalesce(hit.ros12_dollars_per_game, pitch.ros12_dollars_per_game) as double) ros12_dollars_per_game,
    cast(coalesce(hit.rfs12, pitch.rfs12) as int) rfs12,
    cast(coalesce(hit.rfs15, pitch.rfs15) as int) rfs15
from {{ ref('src_nfbc_in_season_players') }} nfbc
left join {{ ref('src_razzball_projections_weekly_hitting') }} hit
    on nfbc.id = hit.nfbcid
left join {{ ref('src_razzball_projections_weekly_pitching') }} pitch
    on nfbc.id = pitch.nfbcid