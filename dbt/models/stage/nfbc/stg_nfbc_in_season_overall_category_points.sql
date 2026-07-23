{{
    config(
        materialized='table'
    )
}}

-- Typed NFBC overall category-points standings. Unused AB/H/IP/ER/BB/HA
-- placeholders from the CSV are dropped. Keeps full snapshot history and
-- marks the latest snapshot per source league key (#182).
with cleaned as (
    select
        {{ nfbc_parse_number('standing_rank', 'int') }} as standing_rank,
        nullif(trim(owner), '') as owner,
        nullif(trim(team), '') as team,
        {{ nfbc_parse_number('league_id', 'int') }} as nfbc_league_id,
        {{ nfbc_parse_number('overall_points', 'double') }} as overall_points,
        {{ nfbc_parse_number('points_back', 'double') }} as points_back,
        {{ nfbc_parse_number('runs_points', 'double') }} as r_pts,
        {{ nfbc_parse_number('home_runs_points', 'double') }} as hr_pts,
        {{ nfbc_parse_number('rbi_points', 'double') }} as rbi_pts,
        {{ nfbc_parse_number('stolen_bases_points', 'double') }} as sb_pts,
        {{ nfbc_parse_number('batting_average_points', 'double') }} as avg_pts,
        {{ nfbc_parse_number('strikeouts_points', 'double') }} as k_pts,
        {{ nfbc_parse_number('wins_points', 'double') }} as w_pts,
        {{ nfbc_parse_number('saves_points', 'double') }} as sv_pts,
        {{ nfbc_parse_number('era_points', 'double') }} as era_pts,
        {{ nfbc_parse_number('whip_points', 'double') }} as whip_pts,
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
    from {{ ref('src_nfbc_in_season_overall_category_points') }}
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
    staged.r_pts,
    staged.hr_pts,
    staged.rbi_pts,
    staged.sb_pts,
    staged.avg_pts,
    staged.k_pts,
    staged.w_pts,
    staged.sv_pts,
    staged.era_pts,
    staged.whip_pts,
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
