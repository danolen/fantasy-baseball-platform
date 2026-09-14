{{
    config(
        materialized='table'
    )
}}

-- Overall-contest category mobility from actual cutlines (#183, #205).
-- Primary signals: local marginal slope (±25 category points), nearest
-- distinct point islands, and the 10/25/50/100 ladder.
-- Optional +1/+5 probes are included for density inspection only.
--
-- #205: when a team sits near the top/bottom of a category, the preferred
-- ±25 window may not exist. Clamp the missing anchor to the field extreme
-- and flag slope_is_clamped rather than returning NULL. Ladder rungs beyond
-- remaining headroom are 'clamped' (partial headroom) or 'maxed' (none).
-- overall_points_per_raw_unit is the reciprocal consumers need for weighting.
-- It is priced per raw_unit_size (0.01 ERA / 0.005 WHIP / 0.001 AVG / 1.0
-- counting), so consumers must divide a raw delta by raw_unit_size before
-- multiplying. See weekly_category_plan.overall_points_for_raw_delta.

{% set slope_window = 25 %}
{% set ladder_deltas = [1, 5, 10, 25, 50, 100] %}

with base as (
    select *
    from {{ ref('stg_nfbc_overall_category_long') }}
),

point_levels as (
    select
        contest_key,
        snapshot_date,
        category,
        higher_is_better,
        category_points as point_level,
        min(raw_stat) as min_raw_at_level,
        max(raw_stat) as max_raw_at_level,
        count(*) as teams_at_level
    from base
    group by
        contest_key,
        snapshot_date,
        category,
        higher_is_better,
        category_points
),

-- raw_needed to reach at least this point_level (exact NFBC points island).
thresholds as (
    select
        contest_key,
        snapshot_date,
        category,
        higher_is_better,
        point_level as target_points,
        case
            when higher_is_better then
                min(min_raw_at_level) over (
                    partition by contest_key, snapshot_date, category
                    order by point_level
                    rows between current row and unbounded following
                )
            else
                max(max_raw_at_level) over (
                    partition by contest_key, snapshot_date, category
                    order by point_level
                    rows between current row and unbounded following
                )
        end as raw_needed
    from point_levels
),

field_extrema as (
    select
        contest_key,
        snapshot_date,
        category,
        max(target_points) as field_max_points,
        min(target_points) as field_min_points
    from thresholds
    group by contest_key, snapshot_date, category
),

field_max_thr as (
    select
        t.contest_key,
        t.snapshot_date,
        t.category,
        t.target_points as field_max_points,
        t.raw_needed as field_max_raw
    from thresholds t
    inner join field_extrema fe
        on t.contest_key = fe.contest_key
        and t.snapshot_date = fe.snapshot_date
        and t.category = fe.category
        and t.target_points = fe.field_max_points
),

field_min_thr as (
    select
        t.contest_key,
        t.snapshot_date,
        t.category,
        t.target_points as field_min_points,
        t.raw_needed as field_min_raw
    from thresholds t
    inner join field_extrema fe
        on t.contest_key = fe.contest_key
        and t.snapshot_date = fe.snapshot_date
        and t.category = fe.category
        and t.target_points = fe.field_min_points
),

nearest_above as (
    select
        b.contest_key,
        b.snapshot_date,
        b.team_key,
        b.category,
        min(p.point_level) as points_above
    from base b
    inner join point_levels p
        on b.contest_key = p.contest_key
        and b.snapshot_date = p.snapshot_date
        and b.category = p.category
        and p.point_level > b.category_points
    group by b.contest_key, b.snapshot_date, b.team_key, b.category
),

nearest_below as (
    select
        b.contest_key,
        b.snapshot_date,
        b.team_key,
        b.category,
        max(p.point_level) as points_below
    from base b
    inner join point_levels p
        on b.contest_key = p.contest_key
        and b.snapshot_date = p.snapshot_date
        and b.category = p.category
        and p.point_level < b.category_points
    group by b.contest_key, b.snapshot_date, b.team_key, b.category
),

ladder_targets as (
    select
        b.*,
        d.delta_points,
        b.category_points + d.delta_points as desired_points_up,
        b.category_points - d.delta_points as desired_points_down
    from base b
    cross join (
        {% for delta in ladder_deltas %}
        select {{ delta }} as delta_points
        {% if not loop.last %}union all{% endif %}
        {% endfor %}
    ) d
),

ladder_up_preferred as (
    select
        lt.contest_key,
        lt.snapshot_date,
        lt.team_key,
        lt.category,
        lt.delta_points,
        t.target_points as cutline_points,
        t.raw_needed as cutline_raw,
        row_number() over (
            partition by
                lt.contest_key,
                lt.snapshot_date,
                lt.team_key,
                lt.category,
                lt.delta_points
            order by t.target_points
        ) as rn
    from ladder_targets lt
    inner join thresholds t
        on lt.contest_key = t.contest_key
        and lt.snapshot_date = t.snapshot_date
        and lt.category = t.category
        and t.target_points >= lt.desired_points_up
),

ladder_down as (
    -- Downside: first point island at or below desired_points_down.
    -- raw_needed for that island is the threshold to *remain* at that
    -- level; falling below it means the next worse island.
    select
        lt.contest_key,
        lt.snapshot_date,
        lt.team_key,
        lt.category,
        lt.delta_points,
        t.target_points as cutline_points,
        t.raw_needed as cutline_raw,
        row_number() over (
            partition by
                lt.contest_key,
                lt.snapshot_date,
                lt.team_key,
                lt.category,
                lt.delta_points
            order by t.target_points desc
        ) as rn
    from ladder_targets lt
    inner join thresholds t
        on lt.contest_key = t.contest_key
        and lt.snapshot_date = t.snapshot_date
        and lt.category = t.category
        and t.target_points <= lt.desired_points_down
),

ladder_up_best as (
    -- Prefer an island at the requested rung; otherwise clamp to the field
    -- max when any headroom remains; otherwise mark maxed (no headroom).
    select
        lt.contest_key,
        lt.snapshot_date,
        lt.team_key,
        lt.category,
        lt.delta_points,
        case
            when pref.cutline_points is not null then pref.cutline_points
            when fm.field_max_points > lt.category_points then fm.field_max_points
            else null
        end as cutline_points,
        case
            when pref.cutline_raw is not null then pref.cutline_raw
            when fm.field_max_points > lt.category_points then fm.field_max_raw
            else null
        end as cutline_raw,
        case
            when pref.cutline_points is not null then 'ok'
            when fm.field_max_points > lt.category_points then 'clamped'
            else 'maxed'
        end as ladder_up_status
    from ladder_targets lt
    left join ladder_up_preferred pref
        on lt.contest_key = pref.contest_key
        and lt.snapshot_date = pref.snapshot_date
        and lt.team_key = pref.team_key
        and lt.category = pref.category
        and lt.delta_points = pref.delta_points
        and pref.rn = 1
    left join field_max_thr fm
        on lt.contest_key = fm.contest_key
        and lt.snapshot_date = fm.snapshot_date
        and lt.category = fm.category
),

ladder_down_best as (
    select * from ladder_down where rn = 1
),

slope_up_preferred as (
    select
        b.contest_key,
        b.snapshot_date,
        b.team_key,
        b.category,
        t.target_points as slope_up_points,
        t.raw_needed as slope_up_raw,
        row_number() over (
            partition by b.contest_key, b.snapshot_date, b.team_key, b.category
            order by t.target_points
        ) as rn
    from base b
    inner join thresholds t
        on b.contest_key = t.contest_key
        and b.snapshot_date = t.snapshot_date
        and b.category = t.category
        and t.target_points >= b.category_points + {{ slope_window }}
),

slope_down_preferred as (
    select
        b.contest_key,
        b.snapshot_date,
        b.team_key,
        b.category,
        t.target_points as slope_down_points,
        t.raw_needed as slope_down_raw,
        row_number() over (
            partition by b.contest_key, b.snapshot_date, b.team_key, b.category
            order by t.target_points desc
        ) as rn
    from base b
    inner join thresholds t
        on b.contest_key = t.contest_key
        and b.snapshot_date = t.snapshot_date
        and b.category = t.category
        and t.target_points <= b.category_points - {{ slope_window }}
),

enriched as (
    select
        b.*,
        na.points_above,
        ta.raw_needed as raw_above,
        nb.points_below,
        tb.raw_needed as raw_below,

        -- Upper slope anchor: preferred +window, else field max, else own
        -- position when already at the top (one-sided slope).
        case
            when su.slope_up_points is not null then su.slope_up_points
            when fm.field_max_points > b.category_points then fm.field_max_points
            else b.category_points
        end as slope_up_points,
        case
            when su.slope_up_raw is not null then su.slope_up_raw
            when fm.field_max_points > b.category_points then fm.field_max_raw
            else b.raw_stat
        end as slope_up_raw,
        case
            when su.slope_up_points is not null then false
            else true
        end as slope_up_is_clamped,

        -- Lower slope anchor: preferred -window, else field min, else own.
        case
            when sd.slope_down_points is not null then sd.slope_down_points
            when fn.field_min_points < b.category_points then fn.field_min_points
            else b.category_points
        end as slope_down_points,
        case
            when sd.slope_down_raw is not null then sd.slope_down_raw
            when fn.field_min_points < b.category_points then fn.field_min_raw
            else b.raw_stat
        end as slope_down_raw,
        case
            when sd.slope_down_points is not null then false
            else true
        end as slope_down_is_clamped

        {% for delta in ladder_deltas %}
        ,
        lu{{ delta }}.cutline_points as cutline_points_up_{{ delta }},
        lu{{ delta }}.cutline_raw as cutline_raw_up_{{ delta }},
        lu{{ delta }}.ladder_up_status as ladder_up_status_{{ delta }},
        ld{{ delta }}.cutline_points as cutline_points_down_{{ delta }},
        ld{{ delta }}.cutline_raw as cutline_raw_down_{{ delta }}
        {% endfor %}
    from base b
    left join nearest_above na
        on b.contest_key = na.contest_key
        and b.snapshot_date = na.snapshot_date
        and b.team_key = na.team_key
        and b.category = na.category
    left join thresholds ta
        on b.contest_key = ta.contest_key
        and b.snapshot_date = ta.snapshot_date
        and b.category = ta.category
        and na.points_above = ta.target_points
    left join nearest_below nb
        on b.contest_key = nb.contest_key
        and b.snapshot_date = nb.snapshot_date
        and b.team_key = nb.team_key
        and b.category = nb.category
    left join thresholds tb
        on b.contest_key = tb.contest_key
        and b.snapshot_date = tb.snapshot_date
        and b.category = tb.category
        and nb.points_below = tb.target_points
    left join slope_up_preferred su
        on b.contest_key = su.contest_key
        and b.snapshot_date = su.snapshot_date
        and b.team_key = su.team_key
        and b.category = su.category
        and su.rn = 1
    left join slope_down_preferred sd
        on b.contest_key = sd.contest_key
        and b.snapshot_date = sd.snapshot_date
        and b.team_key = sd.team_key
        and b.category = sd.category
        and sd.rn = 1
    left join field_max_thr fm
        on b.contest_key = fm.contest_key
        and b.snapshot_date = fm.snapshot_date
        and b.category = fm.category
    left join field_min_thr fn
        on b.contest_key = fn.contest_key
        and b.snapshot_date = fn.snapshot_date
        and b.category = fn.category
    {% for delta in ladder_deltas %}
    left join ladder_up_best lu{{ delta }}
        on b.contest_key = lu{{ delta }}.contest_key
        and b.snapshot_date = lu{{ delta }}.snapshot_date
        and b.team_key = lu{{ delta }}.team_key
        and b.category = lu{{ delta }}.category
        and lu{{ delta }}.delta_points = {{ delta }}
    left join ladder_down_best ld{{ delta }}
        on b.contest_key = ld{{ delta }}.contest_key
        and b.snapshot_date = ld{{ delta }}.snapshot_date
        and b.team_key = ld{{ delta }}.team_key
        and b.category = ld{{ delta }}.category
        and ld{{ delta }}.delta_points = {{ delta }}
    {% endfor %}
),

calc as (
    select
        enriched.*,
        case
            when raw_above is null then null
            when higher_is_better then raw_above - raw_stat
            else raw_stat - raw_above
        end as raw_gap_above,
        case
            when raw_below is null then null
            when higher_is_better then raw_stat - raw_below
            else raw_below - raw_stat
        end as raw_gap_below,

        (slope_up_is_clamped or slope_down_is_clamped) as slope_is_clamped,

        case
            when points_above is null then 'maxed'
            when slope_up_is_clamped then 'partial'
            else 'open'
        end as headroom_status,

        case
            when slope_up_points = slope_down_points then null
            when higher_is_better then
                (slope_up_raw - slope_down_raw)
                / nullif(cast(slope_up_points - slope_down_points as double), 0)
            else
                (slope_down_raw - slope_up_raw)
                / nullif(cast(slope_up_points - slope_down_points as double), 0)
        end as raw_per_category_point

        {% for delta in ladder_deltas %}
        ,
        case
            when cutline_raw_up_{{ delta }} is null then null
            when higher_is_better then cutline_raw_up_{{ delta }} - raw_stat
            else raw_stat - cutline_raw_up_{{ delta }}
        end as raw_gap_up_{{ delta }},
        case
            when cutline_raw_down_{{ delta }} is null then null
            when higher_is_better then raw_stat - cutline_raw_down_{{ delta }}
            else cutline_raw_down_{{ delta }} - raw_stat
        end as raw_gap_down_{{ delta }}
        {% endfor %}
    from enriched
),

with_units as (
    select
        calc.*,
        case
            when category = 'AVG' then 0.001
            when category = 'ERA' then 0.01
            when category = 'WHIP' then 0.005
            else 1.0
        end as raw_unit_size,
        case
            when raw_per_category_point is null then null
            else
                case
                    when category = 'AVG' then 0.001
                    when category = 'ERA' then 0.01
                    when category = 'WHIP' then 0.005
                    else 1.0
                end
                / nullif(raw_per_category_point, 0)
        end as overall_points_per_raw_unit
    from calc
),

with_metrics as (
select
    contest_key,
    source_league_key,
    format,
    nfbc_overall_game_type_id,
    snapshot_date,
    is_latest_snapshot,
    team_key,
    owner,
    team,
    nfbc_league_id,
    overall_rank,
    overall_points,
    category,
    higher_is_better,
    is_ratio,
    raw_stat,
    category_points,
    category_rank,
    category_percentile,
    volume_ab,
    volume_h,
    volume_ip,
    volume_er,
    volume_bb_h,

    points_above,
    raw_above,
    raw_gap_above,
    points_below,
    raw_below,
    raw_gap_below,

    headroom_status,

    {{ slope_window }} as slope_window_points,
    slope_up_points,
    slope_up_raw,
    slope_down_points,
    slope_down_raw,
    slope_up_is_clamped,
    slope_down_is_clamped,
    slope_is_clamped,
    raw_per_category_point,
    raw_unit_size,
    overall_points_per_raw_unit,

    {% for delta in ladder_deltas %}
    cutline_points_up_{{ delta }},
    cutline_raw_up_{{ delta }},
    raw_gap_up_{{ delta }},
    ladder_up_status_{{ delta }},
    cutline_points_down_{{ delta }},
    cutline_raw_down_{{ delta }},
    raw_gap_down_{{ delta }}{% if not loop.last %},{% endif %}
    {% endfor %}
    ,

    case
        when not is_ratio then raw_gap_above
        when category = 'AVG' and volume_ab is not null and raw_above is not null then
            (raw_above * volume_ab) - volume_h
        when category = 'ERA' and volume_ip is not null and volume_ip > 0 and raw_above is not null then
            volume_er - (raw_above * volume_ip / 9.0)
        when category = 'WHIP' and volume_ip is not null and volume_ip > 0 and raw_above is not null then
            volume_bb_h - (raw_above * volume_ip)
        else null
    end as count_equiv_gap_above,

    case
        when not is_ratio then raw_gap_up_10
        when category = 'AVG' and volume_ab is not null and cutline_raw_up_10 is not null then
            (cutline_raw_up_10 * volume_ab) - volume_h
        when category = 'ERA' and volume_ip is not null and volume_ip > 0 and cutline_raw_up_10 is not null then
            volume_er - (cutline_raw_up_10 * volume_ip / 9.0)
        when category = 'WHIP' and volume_ip is not null and volume_ip > 0 and cutline_raw_up_10 is not null then
            volume_bb_h - (cutline_raw_up_10 * volume_ip)
        else null
    end as count_equiv_up_10,

    case
        when not is_ratio then raw_gap_up_25
        when category = 'AVG' and volume_ab is not null and cutline_raw_up_25 is not null then
            (cutline_raw_up_25 * volume_ab) - volume_h
        when category = 'ERA' and volume_ip is not null and volume_ip > 0 and cutline_raw_up_25 is not null then
            volume_er - (cutline_raw_up_25 * volume_ip / 9.0)
        when category = 'WHIP' and volume_ip is not null and volume_ip > 0 and cutline_raw_up_25 is not null then
            volume_bb_h - (cutline_raw_up_25 * volume_ip)
        else null
    end as count_equiv_up_25,

    case
        when not is_ratio then raw_gap_up_50
        when category = 'AVG' and volume_ab is not null and cutline_raw_up_50 is not null then
            (cutline_raw_up_50 * volume_ab) - volume_h
        when category = 'ERA' and volume_ip is not null and volume_ip > 0 and cutline_raw_up_50 is not null then
            volume_er - (cutline_raw_up_50 * volume_ip / 9.0)
        when category = 'WHIP' and volume_ip is not null and volume_ip > 0 and cutline_raw_up_50 is not null then
            volume_bb_h - (cutline_raw_up_50 * volume_ip)
        else null
    end as count_equiv_up_50,

    case
        when not is_ratio then raw_gap_up_100
        when category = 'AVG' and volume_ab is not null and cutline_raw_up_100 is not null then
            (cutline_raw_up_100 * volume_ab) - volume_h
        when category = 'ERA' and volume_ip is not null and volume_ip > 0 and cutline_raw_up_100 is not null then
            volume_er - (cutline_raw_up_100 * volume_ip / 9.0)
        when category = 'WHIP' and volume_ip is not null and volume_ip > 0 and cutline_raw_up_100 is not null then
            volume_bb_h - (cutline_raw_up_100 * volume_ip)
        else null
    end as count_equiv_up_100

from with_units
),

with_cluster as (
    select
        wm.*,
        pl.teams_at_level as teams_at_current_points,
        pl.min_raw_at_level as raw_min_at_current_points,
        pl.max_raw_at_level as raw_max_at_current_points,
        cast(pl.max_raw_at_level - pl.min_raw_at_level as double) as tie_cluster_raw_width
    from with_metrics wm
    left join point_levels pl
        on wm.contest_key = pl.contest_key
        and wm.snapshot_date = pl.snapshot_date
        and wm.category = pl.category
        and wm.category_points = pl.point_level
)

select * from with_cluster
