{{
    config(
        materialized='table'
    )
}}

-- Per-hitter weekly lineup input data. This mart prepares data; the
-- greedy assignment + MILP optimizer (Phase 1a v1-v3) live in the
-- Streamlit app so we have a single source of truth for optimization
-- logic.
--
-- Scope of v2:
--   - Hitters only (pitcher streaming is Phase 1c)
--   - Monday-lock columns (dollars_monday_thursday) for the Mon lineup
--   - Friday-lock columns (weekend_* from the Razzball weekend Hittertron
--     file) for the Fri-Sun lineup. has_weekend_projection = 0 when the
--     weekend file hasn't been uploaded yet for the current partition.
--
-- One row per (league, owner, nfbc_id). Free-agent pool is included so
-- the app can offer "what if you add player X" comparisons later.

with league_formats as (
    select
        league,
        -- ftn_league_size is null for draft-and-hold leagues (nolen_50).
        -- dbt-athena loads the seed column as integer, so empty CSV cells
        -- arrive as SQL NULL and no nullif() is needed here.
        cast(ftn_league_size as int) as ftn_league_size,
        format
    from {{ ref('league_config') }}
),

weekend as (
    -- Razzball dedicated Fri-Sun projection file. Not a split of the
    -- Mon-Sun file: it's a separate projection run with per-game SP
    -- matchups in `sp`. Cast numerics defensively since the source is a
    -- CSV external table.
    select
        cast(nfbcid as varchar) as nfbc_id,
        cast(nullif(num_g, '')  as int)    as weekend_num_g,
        cast(nullif(hg, '')     as int)    as weekend_home_games,
        cast(nullif(ag, '')     as int)    as weekend_away_games,
        cast(nullif(vr, '')     as int)    as weekend_vs_rhp,
        cast(nullif(vl, '')     as int)    as weekend_vs_lhp,
        cast(nullif(dollars, '') as double) as weekend_dollars,
        opp  as weekend_opp,
        sp   as weekend_sp_raw,
        fri  as weekend_week_start_date
    from {{ ref('src_razzball_projections_weekend_hitting') }}
    where nfbcid is not null
),

hitters as (
    select
        wp.id as nfbc_id,
        wp.first_name,
        wp.last_name,
        wp.pos as pos_raw,
        wp.team,
        wp.owner,
        wp.own_pct,
        wp.league,
        wp.week_of,
        wp.bats,
        wp.num_g,
        wp.home_games,
        wp.away_games,
        wp.vs_rhp,
        wp.vs_lhp,
        wp.dollars,
        wp.dollars_per_game,
        wp.dollars_monday_thursday,
        wp.dollars_friday_sunday,
        wp.ros_oc,
        wp.ros_me,
        wp.ros_50,
        wknd.weekend_num_g,
        wknd.weekend_home_games,
        wknd.weekend_away_games,
        wknd.weekend_vs_rhp,
        wknd.weekend_vs_lhp,
        wknd.weekend_dollars,
        wknd.weekend_opp,
        wknd.weekend_sp_raw,
        wknd.weekend_week_start_date,
        -- Normalize the comma-separated position string: uppercase, trim
        -- each token, collapse whitespace. NFBC uses values like
        -- "1B,OF" or "2B, SS"; we produce array ['1B','OF'] or ['2B','SS'].
        transform(
            split(upper(coalesce(wp.pos, '')), ','),
            p -> trim(p)
        ) as pos_array
    from {{ ref('mart_weekly_projections') }} wp
    left join weekend wknd
        on cast(wp.id as varchar) = wknd.nfbc_id
    -- Require weekly hitting projection data. num_g populates only from
    -- the Razzball weekly hitting file, so this is the cleanest hitter
    -- filter available without re-joining source tables.
    where wp.num_g is not null
)

select
    h.league,
    lf.format,
    lf.ftn_league_size,
    h.owner,
    cast(h.own_pct as int) as own_pct,
    h.nfbc_id,
    trim(h.first_name) as first_name,
    trim(h.last_name) as last_name,
    concat(trim(h.first_name), ' ', trim(h.last_name)) as player_name,
    h.team,
    h.pos_raw,
    h.pos_array,
    h.bats,
    h.week_of,
    cast(h.num_g as int) as num_g,
    cast(h.home_games as int) as home_games,
    cast(h.away_games as int) as away_games,
    cast(h.vs_rhp as int) as vs_rhp,
    cast(h.vs_lhp as int) as vs_lhp,
    cast(h.dollars as double) as dollars,
    cast(h.dollars_per_game as double) as dollars_per_game,
    cast(h.dollars_monday_thursday as double) as dollars_monday_thursday,
    cast(h.dollars_friday_sunday as double) as dollars_friday_sunday,
    -- Fri-Sun projections from the dedicated weekend Hittertron file.
    -- NULL when the weekend partition for this week hasn't been uploaded
    -- yet; has_weekend_projection gives the app a cheap existence check.
    h.weekend_num_g,
    h.weekend_home_games,
    h.weekend_away_games,
    h.weekend_vs_rhp,
    h.weekend_vs_lhp,
    h.weekend_dollars,
    h.weekend_opp,
    h.weekend_sp_raw,
    h.weekend_week_start_date,
    cast(case when h.weekend_dollars is not null then 1 else 0 end as int)
        as has_weekend_projection,
    cast(
        case lf.format
            when 'oc' then h.ros_oc
            when 'me' then h.ros_me
            when '50s' then h.ros_50
        end as double
    ) as ros_value,
    -- Per-slot eligibility flags. NFBC slot rules:
    --   C/1B/2B/3B/SS/OF = exact position match
    --   MI               = 2B or SS
    --   CI               = 1B or 3B
    --   UTIL             = any hitter
    cast(contains(h.pos_array, 'C')  as int) as is_c_eligible,
    cast(contains(h.pos_array, '1B') as int) as is_1b_eligible,
    cast(contains(h.pos_array, '2B') as int) as is_2b_eligible,
    cast(contains(h.pos_array, '3B') as int) as is_3b_eligible,
    cast(contains(h.pos_array, 'SS') as int) as is_ss_eligible,
    cast(contains(h.pos_array, 'OF') as int) as is_of_eligible,
    cast(
        (contains(h.pos_array, '2B') or contains(h.pos_array, 'SS')) as int
    ) as is_mi_eligible,
    cast(
        (contains(h.pos_array, '1B') or contains(h.pos_array, '3B')) as int
    ) as is_ci_eligible,
    1 as is_util_eligible
from hitters h
inner join league_formats lf
    on h.league = lf.league
