{{
    config(
        materialized='table'
    )
}}

-- Typed NFBC overall overview standings. Keeps full snapshot history and
-- marks the latest snapshot per source league key (#182).
with cleaned as (
    select
        {{ nfbc_parse_number('standing_rank', 'int') }} as standing_rank,
        nullif(trim(owner), '') as owner,
        nullif(trim(team), '') as team,
        {{ nfbc_parse_number('league_id', 'int') }} as nfbc_league_id,
        {{ nfbc_parse_number('draft_position', 'int') }} as draft_position,
        {{ nfbc_parse_number('hitting_points', 'double') }} as hitting_points,
        {{ nfbc_parse_number('pitching_points', 'double') }} as pitching_points,
        {{ nfbc_parse_number('overall_points', 'double') }} as overall_points,
        {{ nfbc_parse_number('points_change', 'double') }} as points_change,
        {{ nfbc_parse_number('rank_change', 'int') }} as rank_change,
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
    from {{ ref('src_nfbc_in_season_overall_overview') }}
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
    staged.draft_position,
    staged.hitting_points,
    staged.pitching_points,
    staged.overall_points,
    staged.points_change,
    staged.rank_change,
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
