{{
    config(
        materialized='table'
    )
}}

with league_config as (
    select
        league,
        -- ftn_league_size is null for draft-and-hold leagues (nolen_50).
        -- dbt-athena loads the seed column as integer, so empty CSV cells
        -- arrive as SQL NULL; the inner join to stg_ftn_faab below drops
        -- those rows naturally so no FAAB data gets attached.
        cast(ftn_league_size as int) as ftn_league_size,
        format
    from {{ ref('league_config') }}
),

my_faab as (
    -- Manually-maintained per-league budget remaining. Update weekly after
    -- waivers run in dbt/seeds/faab_remaining.csv, then re-seed. Competitor
    -- FAAB (opponents' remaining budgets) needs an NFBC scrape and lands
    -- in Phase 2b, not here.
    select
        league,
        cast(my_faab_remaining as int) as my_faab_remaining,
        cast(as_of_date as varchar) as faab_as_of_date
    from {{ ref('faab_remaining') }}
),

weekly as (
    select wp.*, lc.format, mf.my_faab_remaining, mf.faab_as_of_date
    from {{ ref('mart_weekly_projections') }} wp
    inner join league_config lc
        on wp.league = lc.league
    left join my_faab mf
        on wp.league = mf.league
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
        ftn.notes_sp_matchups as ftn_notes,
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
    cast(wp.own_pct as int) as own_pct,
    wp.week_of,
    wp.opps,
    wp.next_proj_opps,
    cast(wp.num_g as int) as num_g,
    wp.bats,
    cast(wp.home_games as int) as home_games,
    cast(wp.away_games as int) as away_games,
    cast(wp.vs_rhp as int) as vs_rhp,
    cast(wp.vs_lhp as int) as vs_lhp,
    cast(wp.dollars as double) as dollars,
    cast(wp.dollars_per_game as double) as dollars_per_game,
    cast(wp.dollars_monday_thursday as double) as dollars_monday_thursday,
    cast(wp.dollars_friday_sunday as double) as dollars_friday_sunday,
    cast(wp.roster_pct as int) as roster_pct,
    cast(wp.ros12_dollars_per_game as double) as ros12_dollars_per_game,
    cast(wp.rfs12 as int) as rfs12,
    cast(wp.rfs15 as int) as rfs15,
    cast(case wp.format
        when 'oc' then wp.ros_oc
        when 'me' then wp.ros_me
        when '50s' then wp.ros_50
    end as double) as ros_value,
    ftn.ftn_type,
    cast(ftn.low_bid as int) as low_bid,
    cast(ftn.high_bid as int) as high_bid,
    ftn.ftn_notes,
    ftn.bid_change,
    ftn.status_tag,
    cast(case when ftn.nfbc_id is not null then 1 else 0 end as int) as has_ftn_rec,
    wp.my_faab_remaining,
    wp.faab_as_of_date,
    -- What fraction of your remaining budget this bid would consume. Null
    -- for draft-and-hold (nolen_50 with my_faab_remaining=0) and for
    -- FTN-only rows with no matching weekly (where wp.* is all null).
    cast(
        case
            when wp.my_faab_remaining is null or wp.my_faab_remaining <= 0 then null
            else cast(ftn.high_bid as double) / wp.my_faab_remaining * 100
        end as double
    ) as high_bid_pct_of_faab
from weekly wp
full outer join ftn_by_league ftn
    on wp.id = ftn.nfbc_id
    and wp.league = ftn.league
