{{
    config(
        materialized='table'
    )
}}

-- Explode the Razzball weekend Hittertron `sp` column into one row per
-- (hitter, game_date, opposing_sp). The source column is a space-separated
-- list of tokens of the form `M/D|NameWithLeadingInitial(HAND)`, e.g.
--   `4/17|RFeltner(R) 4/18|JHerget(R) 4/19|MLorenzen(R)`
--
-- Feeds the weekly lineup optimizer (Phase 1a v3) so we can reason about
-- per-game platoon splits instead of the aggregated vR/vL counts.

with weekend_source as (
    select
        cast(nfbcid as varchar) as nfbcid,
        razzid,
        name,
        b as bats,
        team,
        fri as week_start_date,
        sp as sp_raw,
        _filename,
        _ptkey
    from {{ ref('src_razzball_projections_weekend_hitting') }}
    where sp is not null
      and sp != ''
),

sp_entries as (
    select
        nfbcid,
        razzid,
        name,
        bats,
        team,
        week_start_date,
        trim(sp_entry) as sp_entry,
        _filename,
        _ptkey
    from weekend_source
    cross join unnest(split(sp_raw, ' ')) as t(sp_entry)
    where trim(sp_entry) != ''
),

parsed as (
    select
        nfbcid,
        razzid,
        name,
        bats,
        team,
        week_start_date,
        regexp_extract(sp_entry, '^([0-9]+/[0-9]+)', 1) as game_date_str,
        regexp_extract(sp_entry, '\|([^(]+)\(', 1) as opposing_sp_token,
        regexp_extract(sp_entry, '\(([RLS])\)', 1) as opposing_sp_hand,
        sp_entry,
        _filename,
        _ptkey
    from sp_entries
)

select
    nfbcid,
    razzid,
    name,
    bats,
    team,
    week_start_date,
    game_date_str,
    opposing_sp_token,
    opposing_sp_hand,
    case
        when bats = 'R' and opposing_sp_hand = 'L' then 'platoon_advantage'
        when bats = 'L' and opposing_sp_hand = 'R' then 'platoon_advantage'
        when bats = 'S' then 'switch'
        when bats = 'R' and opposing_sp_hand = 'R' then 'platoon_disadvantage'
        when bats = 'L' and opposing_sp_hand = 'L' then 'platoon_disadvantage'
        else 'unknown'
    end as platoon_flag,
    sp_entry as sp_raw_entry,
    _filename,
    _ptkey
from parsed
where opposing_sp_token is not null
  and opposing_sp_token != ''
