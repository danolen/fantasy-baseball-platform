{{
    config(
        materialized='table'
    )
}}

-- Weekly owned-roster membership from daily NFBC snapshots (#185).
--
-- Grain: one row per (league, owner, nfbc_id, scoring week) for owned
-- players only (free-agent rows have an empty owner and are excluded).
-- The weekly anchor is the FIRST snapshot of each Monday-start week, i.e.
-- the first roster file after Sunday 10pm ET FAAB processing, so each row
-- is the post-FAAB roster for that scoring week.
--
-- The pre-existing stg_nfbc_in_season_players_snapshots (#206) cannot serve
-- here: it drops owner and dedupes across leagues. This model preserves
-- (league, owner) so retention and churn are measurable per team.
-- 2026 season only; 50s leagues are kept here but excluded downstream
-- (no FAAB means no retain/drop decisions).

with raw as (
    select
        regexp_replace(_filename, '(?i)\.csv$', '') as league,
        nullif(trim(owner), '') as owner,
        cast(nullif(trim(id), '') as varchar) as nfbc_id,
        nullif(trim(players), '') as player_name,
        nullif(trim(pos), '') as pos_raw,
        nullif(trim(team), '') as team,
        nullif(trim(at_bats), '') as at_bats,
        nullif(trim(innings_pitched), '') as innings_pitched,
        date(
            date_parse(
                concat(year, '-', lpad(month, 2, '0'), '-', lpad(day, 2, '0')),
                '%Y-%m-%d'
            )
        ) as snapshot_date
    from {{ ref('src_nfbc_in_season_players_history') }}
),

season as (
    select *
    from raw
    where snapshot_date >= date '2026-03-27'
        and owner is not null
        and nfbc_id is not null
),

-- First snapshot per (league, Monday-start week): the post-FAAB roster.
-- dense_rank keeps every roster row from that first day (row_number would
-- keep a single row per league-week).
weekly_anchor as (
    select
        season.*,
        date_trunc('week', snapshot_date) as week_key,
        dense_rank() over (
            partition by league, date_trunc('week', snapshot_date)
            order by snapshot_date
        ) as _day_rn
    from season
),

anchored as (
    select *
    from weekly_anchor
    where _day_rn = 1
),

typed as (
    select
        league,
        owner,
        nfbc_id,
        max(player_name) as player_name,
        max(pos_raw) as pos_raw,
        max(team) as team,
        max(snapshot_date) as snapshot_date,
        week_key,
        -- Pitcher iff the pos tokens are pitching-only. 'UT,P' (Ohtani)
        -- stays a hitter row; team-code/blank pos quirks fall back to
        -- which stat line is present.
        max(
            case
                when cardinality(
                    array_intersect(
                        transform(
                            split(upper(coalesce(pos_raw, '')), ','),
                            p -> trim(p)
                        ),
                        array['P', 'SP', 'RP']
                    )
                ) > 0
                and cardinality(
                    array_intersect(
                        transform(
                            split(upper(coalesce(pos_raw, '')), ','),
                            p -> trim(p)
                        ),
                        array['C', '1B', '2B', '3B', 'SS', 'OF', 'UT', 'DH']
                    )
                ) = 0 then 'pitcher'
                when pos_raw is null
                    or trim(pos_raw) = ''
                    or cardinality(
                        array_intersect(
                            transform(
                                split(upper(pos_raw), ','),
                                p -> trim(p)
                            ),
                            array['C', '1B', '2B', '3B', 'SS', 'OF', 'UT', 'DH', 'P', 'SP', 'RP']
                        )
                    ) = 0 then
                    case
                        when innings_pitched is not null and at_bats is null then 'pitcher'
                        else 'hitter'
                    end
                else 'hitter'
            end
        ) as row_type
    from anchored
    group by league, owner, nfbc_id, week_key
)

select
    typed.league,
    typed.owner,
    typed.nfbc_id,
    typed.player_name,
    typed.pos_raw,
    typed.team,
    typed.row_type,
    -- Primary scarcity group (C first: a C,1B player covers C).
    case
        when row_type = 'pitcher' then 'P'
        when cardinality(
            array_intersect(
                transform(split(upper(coalesce(pos_raw, '')), ','), p -> trim(p)),
                array['C']
            )
        ) > 0 then 'C'
        when cardinality(
            array_intersect(
                transform(split(upper(coalesce(pos_raw, '')), ','), p -> trim(p)),
                array['2B', 'SS']
            )
        ) > 0 then 'MI'
        when cardinality(
            array_intersect(
                transform(split(upper(coalesce(pos_raw, '')), ','), p -> trim(p)),
                array['1B', '3B']
            )
        ) > 0 then 'CI'
        when cardinality(
            array_intersect(
                transform(split(upper(coalesce(pos_raw, '')), ','), p -> trim(p)),
                array['OF']
            )
        ) > 0 then 'OF'
        else 'UT'
    end as pos_group,
    snapshot_date,
    week_key
from typed
-- Configured leagues only: other managers' files the maintainer can see
-- (PonyUp, GARDENers, …) are not part of this platform's evidence base.
inner join {{ ref('league_config') }} lc
    on lc.league = typed.league
