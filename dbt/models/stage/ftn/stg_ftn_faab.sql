{{
    config(
        materialized='table'
    )
}}

-- Column names reference the Glue table for source('ftn', 'faab').
-- Expected mapping from FTN CSV headers:
--   "Player"                → player
--   "Position"              → position
--   "Team"                  → team
--   "Own%"                  → own_pct
--   "Type"                  → type
--   "Low Bid"               → low_bid
--   "High Bid"              → high_bid
--   "Notes / SP Matchups"   → notes_sp_matchups
-- Adjust column names below if your Glue catalog uses different names.

with ftn_raw as (
    select
        player,
        position,
        team,
        own_pct,
        type,
        low_bid,
        high_bid,
        notes_sp_matchups,
        _filename,
        _ptkey,
        -- Extract bid direction from emojis before stripping them
        case
            when regexp_like(player, '\x{2B06}') then 'raised'
            when regexp_like(player, '\x{2B07}') then 'lowered'
            when regexp_like(player, '\x{1F195}') then 'new'
        end as emoji_bid_change,
        -- Strip non-Latin characters (emojis, variation selectors, symbols)
        -- and asterisks, then collapse runs of whitespace
        regexp_replace(
            trim(replace(regexp_replace(player, '[^\x{0020}-\x{024F}]', ''), '*', '')),
            '\s{2,}', ' '
        ) as player_sanitized
    from {{ ref('src_ftn_faab') }}
),

ftn_step1 as (
    select
        player as player_raw,
        player_sanitized,
        coalesce(
            emoji_bid_change,
            regexp_extract(player_sanitized, '\s+-\s+(raised|lowered|reduced)\s*$', 1)
        ) as bid_change,
        regexp_replace(player_sanitized, '\s+-\s+(raised|lowered|reduced)\s*$', '') as name_after_bid_strip,
        position,
        team,
        own_pct,
        type,
        low_bid,
        high_bid,
        notes_sp_matchups,
        _filename,
        _ptkey
    from ftn_raw
),

ftn_cleaned as (
    select
        player_raw,
        bid_change,
        regexp_extract(name_after_bid_strip, '\(([^)]+)\)\s*$', 1) as status_tag,
        trim(regexp_replace(name_after_bid_strip, '\s*\([^)]*\)\s*$', '')) as player_clean,
        position,
        team,
        cast(nullif(own_pct, '') as int) as own_pct,
        type,
        cast(nullif(low_bid, '') as int) as low_bid,
        cast(nullif(high_bid, '') as int) as high_bid,
        notes_sp_matchups,
        cast(regexp_extract(_filename, '(\d+)\s*[Tt]eam', 1) as int) as league_size,
        _ptkey
    from ftn_step1
),

ftn_keyed as (
    select *,
        lower(regexp_replace(
            normalize(replace(player_clean, '.', ''), NFD),
            '[\x{0300}-\x{036F}]', ''
        )) as match_key
    from ftn_cleaned
),

nfbc_keyed as (
    select
        cast(id as varchar) as nfbc_id,
        lower(regexp_replace(
            normalize(replace(
                concat(
                    trim(split_part(players, ', ', 2)),
                    ' ',
                    trim(split_part(players, ', ', 1))
                ),
                '.', ''
            ), NFD),
            '[\x{0300}-\x{036F}]', ''
        )) as match_key,
        team as nfbc_team
    from {{ ref('src_nfbc_players') }}
),

overrides as (
    select
        ftn_player,
        ftn_team,
        cast(nfbc_id as varchar) as nfbc_id
    from {{ ref('ftn_nfbc_player_overrides') }}
)

select
    coalesce(ovr.nfbc_id, nfbc.nfbc_id) as nfbc_id,
    ftn.player_raw,
    ftn.player_clean,
    ftn.bid_change,
    ftn.status_tag,
    ftn.position,
    ftn.team,
    ftn.own_pct,
    ftn.type,
    ftn.low_bid,
    ftn.high_bid,
    ftn.notes_sp_matchups,
    ftn.league_size,
    ftn._ptkey
from ftn_keyed ftn
left join nfbc_keyed nfbc
    on ftn.match_key = nfbc.match_key
    and ftn.team = nfbc.nfbc_team
left join overrides ovr
    on ftn.player_clean = ovr.ftn_player
    and ftn.team = ovr.ftn_team
