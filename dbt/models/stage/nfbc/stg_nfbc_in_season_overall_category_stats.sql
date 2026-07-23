{{
    config(
        materialized='table'
    )
}}

-- Typed NFBC overall category-stat standings. Keeps full snapshot history and
-- marks the latest snapshot per source league key (#182).
with cleaned as (
    select
        {{ nfbc_parse_number('standing_rank', 'int') }} as standing_rank,
        nullif(trim(owner), '') as owner,
        nullif(trim(team), '') as team,
        {{ nfbc_parse_number('league_id', 'int') }} as nfbc_league_id,
        {{ nfbc_parse_number('overall_points', 'double') }} as overall_points,
        {{ nfbc_parse_number('points_back', 'double') }} as points_back,
        {{ nfbc_parse_number('runs', 'int') }} as r,
        {{ nfbc_parse_number('home_runs', 'int') }} as hr,
        {{ nfbc_parse_number('rbi', 'int') }} as rbi,
        {{ nfbc_parse_number('stolen_bases', 'int') }} as sb,
        {{ nfbc_parse_number('at_bats', 'int') }} as ab,
        {{ nfbc_parse_number('hits', 'int') }} as h,
        {{ nfbc_parse_number('batting_average', 'double') }} as avg,
        {{ nfbc_parse_number('strikeouts', 'int') }} as k,
        {{ nfbc_parse_number('wins', 'int') }} as w,
        {{ nfbc_parse_number('saves', 'int') }} as sv,
        {{ nfbc_parse_number('innings_pitched', 'double') }} as ip,
        {{ nfbc_parse_number('earned_runs', 'int') }} as er,
        {{ nfbc_parse_number('era', 'double') }} as era,
        {{ nfbc_parse_number('walks', 'int') }} as bb,
        {{ nfbc_parse_number('hits_allowed', 'int') }} as ha,
        {{ nfbc_parse_number('whip', 'double') }} as whip,
        year,
        month,
        day,
        _ptkey,
        _filename,
        _loaddatetime,
        regexp_replace(_filename, '(?i)\\.csv$', '') as source_league_key,
        date(
            date_parse(
                concat(
                    year,
                    '-',
                    lpad(month, 2, '0'),
                    '-',
                    lpad(day, 2, '0')
                ),
                '%Y-%m-%d'
            )
        ) as snapshot_date
    from {{ ref('src_nfbc_in_season_overall_category_stats') }}
),

with_latest as (
    select
        cleaned.*,
        snapshot_date = max(snapshot_date) over (
            partition by source_league_key
        ) as is_latest_snapshot
    from cleaned
)

select
    staged.standing_rank,
    staged.owner,
    staged.team,
    staged.nfbc_league_id,
    staged.overall_points,
    staged.points_back,
    staged.r,
    staged.hr,
    staged.rbi,
    staged.sb,
    staged.ab,
    staged.h,
    staged.avg,
    staged.k,
    staged.w,
    staged.sv,
    staged.ip,
    staged.er,
    staged.era,
    staged.bb,
    staged.ha,
    staged.whip,
    staged.source_league_key,
    staged.snapshot_date,
    staged.is_latest_snapshot,
    lc.format,
    cast(lc.nfbc_overall_game_type_id as int) as nfbc_overall_game_type_id,
    staged.year,
    staged.month,
    staged.day,
    staged._ptkey,
    staged._filename,
    staged._loaddatetime
from with_latest staged
left join {{ ref('league_config') }} lc
    on staged.source_league_key = lc.league
