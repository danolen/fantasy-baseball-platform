"""
Fantasy Baseball In-Season Tool

FAAB worksheet + weekly lineup optimizer for NFBC league management.
"""

import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from pyathena import connect
from pyathena.pandas.cursor import PandasCursor

from faab_bid_bucket import BUCKET_LABELS, format_bid_bucket
from faab_budget_pacing import (
    PACING_CAPTION,
    STATUS_LABELS,
    build_pacing_chart,
    load_season_calendar,
    marker_week,
    pacing_snapshot,
    parse_week_date,
    season_week_number,
)
from faab_theoretical_bid import THEORETICAL_BID_CAPTION, THEORETICAL_BID_HELP, attach_theoretical_bid
from faab_what_if import (
    RANK_MODE_OVERALL,
    RANK_MODE_WEEKLY,
    analyze_add_drop,
    format_delta_rows,
    format_projected_stats,
    rank_candidates,
    starters_table,
)
from lineup_optimizer import optimize_week
from lineup_weights import (
    OBJECTIVE_NEUTRAL,
    OBJECTIVE_TEAM_FIT,
    ratio_context_from_plan_rows,
    team_fit_inputs_ready,
    weights_from_plan_rows,
)
from projection_divergence import flag_caption, summarize_player_flags
from team_defaults import default_index, matching_index
from two_start_pitchers import (
    build_two_start_rows,
    schedule_bucket_caption,
)
from ros_rankings import (
    apply_ros_filters,
    format_for_league,
    format_ros_display,
    ros_table_name,
)
from weekly_category_plan import (
    CATEGORY_ORDER,
    DEFAULT_STRETCH,
    STRETCH_OPTIONS,
    build_category_plan_rows,
)

# Display order for the starters table. Intentionally different from the
# greedy fill order (which is most-constrained first) — this is purely a
# UX preference for reading the lineup left-to-right the way it shows up
# on the NFBC roster page.
SLOT_DISPLAY_ORDER = ["C", "1B", "2B", "SS", "3B", "MI", "CI", "OF", "UTIL"]

load_dotenv()

st.set_page_config(
    page_title="In-Season Tool",
    page_icon="⚾",
    layout="wide",
)


def get_config(key, default=None):
    try:
        if "default" in st.secrets and key in st.secrets["default"]:
            return st.secrets["default"][key]
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


ATHENA_SCHEMA = get_config("ATHENA_SCHEMA", "dbt_main")
# dbt_project.yml sends main/stage/source models to schema `dbt_<name>`, but
# seeds have no +schema override so they land in the base profile schema
# (e.g. `dbt`). Override via env/secret if that changes.
ATHENA_SEEDS_SCHEMA = get_config("ATHENA_SEEDS_SCHEMA", "dbt")
ATHENA_STAGE_SCHEMA = get_config("ATHENA_STAGE_SCHEMA", "dbt_stage")
ATHENA_REGION = get_config("ATHENA_REGION", "us-east-1")
ATHENA_S3_OUTPUT = get_config("ATHENA_S3_OUTPUT")
ATHENA_WORKGROUP = get_config("ATHENA_WORKGROUP")

for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"):
    val = get_config(key, ATHENA_REGION if key == "AWS_DEFAULT_REGION" else None)
    if val and not os.getenv(key):
        os.environ[key] = val

if not ATHENA_S3_OUTPUT:
    st.error(
        "**Configuration Error:** `ATHENA_S3_OUTPUT` is required.\n\n"
        "Create a `.env` file with: `ATHENA_S3_OUTPUT=s3://your-bucket/query-results/`"
    )
    st.stop()


LEAGUES = {
    "OC": "nolen_oc",
    "Cash 12": "nolen_cash_12",
    "OCQ": "nolen_ocq",
    "Cash 15": "nolen_cash_15",
    "NFBC 50": "nolen_50",
}


def _optimize_df(df):
    for col in df.select_dtypes(include=["object"]).columns:
        if df[col].nunique() / max(len(df), 1) < 0.5:
            df[col] = df[col].astype("category")
    for col in df.select_dtypes(include=["int64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="integer")
    for col in df.select_dtypes(include=["float64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="float")
    return df


def _connect():
    kwargs = {
        "s3_staging_dir": ATHENA_S3_OUTPUT,
        "region_name": ATHENA_REGION,
        "schema_name": ATHENA_SCHEMA,
        "cursor_class": PandasCursor,
    }
    if ATHENA_WORKGROUP:
        kwargs["work_group"] = ATHENA_WORKGROUP
    return connect(**kwargs)


@st.cache_data(ttl=900)
def load_faab_data(league):
    query = f"""
        SELECT * FROM {ATHENA_SCHEMA}.mart_faab_worksheet
        WHERE league = '{league}'
    """
    return _optimize_df(_connect().cursor().execute(query).as_pandas())


@st.cache_data(ttl=900)
def load_unmatched():
    query = f"SELECT * FROM {ATHENA_SCHEMA}.mart_faab_unmatched"
    return _connect().cursor().execute(query).as_pandas()


@st.cache_data(ttl=900)
def load_lineup_inputs(league):
    query = f"""
        SELECT * FROM {ATHENA_SCHEMA}.mart_weekly_lineup_inputs
        WHERE league = '{league}'
    """
    return _connect().cursor().execute(query).as_pandas()


@st.cache_data(ttl=3600)
def load_roster_slots():
    query = f"SELECT * FROM {ATHENA_SEEDS_SCHEMA}.league_roster_slots"
    return _connect().cursor().execute(query).as_pandas()


@st.cache_data(ttl=3600)
def load_league_config():
    query = f"SELECT * FROM {ATHENA_SEEDS_SCHEMA}.league_config"
    return _connect().cursor().execute(query).as_pandas()


@st.cache_data(ttl=900)
def load_ros_rankings(fmt):
    """Rest-of-season overall rankings for one format mart (#67)."""
    table = ros_table_name(fmt)
    cols = ", ".join(
        [
            "rank",
            "id",
            "name",
            "team",
            "pos",
            "adp",
            "min_pick",
            "max_pick",
            "rank_diff",
            "projected_opening_day_status",
            "value",
            "pa",
            "ab",
            "r",
            "hr",
            "rbi",
            "sb",
            "avg",
            "obp",
            "slg",
            "ip",
            "k",
            "w",
            "sv",
            "era",
            "whip",
        ]
    )
    query = f"SELECT {cols} FROM {ATHENA_SCHEMA}.{table} ORDER BY rank"
    return _optimize_df(_connect().cursor().execute(query).as_pandas())


@st.cache_data(ttl=900)
def load_weekly_category_plan(league):
    query = f"""
        SELECT * FROM {ATHENA_SCHEMA}.mart_weekly_category_plan
        WHERE league = '{league}'
    """
    return _optimize_df(_connect().cursor().execute(query).as_pandas())


@st.cache_data(ttl=900)
def load_overall_overview(league):
    """Latest NFBC overall overview snapshot for one contest (#189 §1)."""
    query = f"""
        SELECT
            standing_rank,
            owner,
            team,
            hitting_points,
            pitching_points,
            overall_points,
            points_change,
            rank_change,
            snapshot_date,
            source_league_key,
            is_latest_snapshot
        FROM {ATHENA_STAGE_SCHEMA}.stg_nfbc_in_season_overall_overview
        WHERE source_league_key = '{league}'
          AND is_latest_snapshot = true
    """
    return _optimize_df(_connect().cursor().execute(query).as_pandas())


@st.cache_data(ttl=900)
def load_category_mobility(league):
    """Latest category mobility rows for one overall contest (#189 §2)."""
    query = f"""
        SELECT *
        FROM {ATHENA_SCHEMA}.mart_overall_category_mobility
        WHERE contest_key = '{league}'
          AND is_latest_snapshot = true
    """
    return _optimize_df(_connect().cursor().execute(query).as_pandas())


def _overall_game_type_id(league_cfg: pd.DataFrame, league: str):
    """Return nfbc_overall_game_type_id when the league has an overall feed."""
    cfg_row = league_cfg[league_cfg["league"] == league]
    if cfg_row.empty:
        return None
    raw_id = cfg_row.iloc[0].get("nfbc_overall_game_type_id")
    if raw_id is None or str(raw_id).strip() in ("", "nan", "None"):
        return None
    return raw_id


def _fmt_signed(val, digits=1):
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if num > 0 else ""
    return f"{sign}{num:.{digits}f}"


@st.cache_data(ttl=900)
def load_projection_divergence():
    """Observability flags only (#206) — never feed optimize_week."""
    query = f"""
        SELECT *
        FROM {ATHENA_SCHEMA}.mart_projection_rate_divergence
        WHERE is_latest_projection = true
    """
    return _optimize_df(_connect().cursor().execute(query).as_pandas())


try:
    unmatched_df = load_unmatched()
except Exception:
    unmatched_df = pd.DataFrame()

unmatched_count = len(unmatched_df)

st.title("⚾ In-Season Tool")
if unmatched_count > 0:
    if st.button(
        f"🟡 {unmatched_count} unmatched FTN players",
        key="unmatched_ftn_badge",
        type="tertiary",
    ):
        st.session_state["open_unmatched_expander"] = True
        st.toast("Open the FAAB Worksheet tab to review unmatched players.")

st.sidebar.header("League")
selected_league = st.sidebar.selectbox("Select League", list(LEAGUES.keys()))
league_key = LEAGUES[selected_league]


tab_faab, tab_lineup, tab_overall, tab_ros = st.tabs(
    ["FAAB Worksheet", "Lineup Optimizer", "Overall Standings", "ROS Rankings"]
)


# ---------------------------------------------------------------------------
# FAAB Worksheet tab
# ---------------------------------------------------------------------------

with tab_faab:
    st.sidebar.header("FAAB Filters")
    ftn_only = st.sidebar.checkbox("FTN recommended only", value=False)

    try:
        df = load_faab_data(league_key)
    except Exception as e:
        st.error(f"Failed to load data from Athena: {e}")
        st.stop()

    try:
        league_cfg_faab = load_league_config()
    except Exception:
        league_cfg_faab = pd.DataFrame()
    df = attach_theoretical_bid(df, format_for_league(league_cfg_faab, league_key))

    # League-level FAAB budget (full table, not sidebar filters) for help UI.
    league_has_faab = (
        "my_faab_remaining" in df.columns
        and df["my_faab_remaining"].notna().any()
        and (
            pd.to_numeric(df["my_faab_remaining"], errors="coerce").fillna(0) > 0
        ).any()
    )

    if league_key == "nolen_50":
        st.info(
            "NFBC 50 is draft-and-hold — no FAAB. This tab still shows weekly "
            "projection data for rostered players; use the Lineup Optimizer tab "
            "for start/sit."
        )

    all_positions = sorted(
        {
            p.strip()
            for pos in df["position"].dropna().unique()
            for p in str(pos).split(",")
        }
    )
    selected_positions = st.sidebar.multiselect(
        "Position", all_positions, default=all_positions
    )

    all_types = sorted(
        df.loc[df["has_ftn_rec"] == 1, "ftn_type"].dropna().unique().tolist()
    )
    selected_types = st.sidebar.multiselect("FTN Type", all_types, default=all_types)

    FREE_AGENT = "Free Agent"
    owner_values = sorted(df["owner"].dropna().loc[df["owner"] != ""].unique().tolist())
    owner_options = [FREE_AGENT] + owner_values
    selected_owners = st.sidebar.multiselect(
        "Owner", owner_options, default=[FREE_AGENT]
    )

    search = st.sidebar.text_input("Search player")

    selected_buckets = []
    if league_has_faab and "bid_bucket" in df.columns:
        selected_buckets = st.sidebar.multiselect(
            "Bid bucket",
            options=["triage", "tactical", "strategic"],
            default=["triage", "tactical", "strategic"],
            format_func=lambda b: BUCKET_LABELS.get(b, b),
        )

    mask = pd.Series(True, index=df.index)

    if ftn_only:
        mask &= df["has_ftn_rec"] == 1

    if selected_positions:
        pos_pattern = "|".join(selected_positions)
        mask &= df["position"].str.contains(pos_pattern, na=False)

    if selected_types and ftn_only:
        mask &= df["ftn_type"].isin(selected_types)

    if selected_owners:
        is_free_agent = df["owner"].isna() | (df["owner"] == "")
        is_selected_owner = df["owner"].isin(
            [o for o in selected_owners if o != FREE_AGENT]
        )
        mask &= (
            is_free_agent if FREE_AGENT in selected_owners else False
        ) | is_selected_owner

    if search:
        mask &= df["player"].str.contains(search, case=False, na=False)

    if selected_buckets:
        mask &= df["bid_bucket"].isin(selected_buckets) | df["bid_bucket"].isna()
    elif league_has_faab and "bid_bucket" in df.columns:
        mask &= False

    display = df.loc[mask].copy()

    has_faab = (
        "my_faab_remaining" in display.columns
        and display["my_faab_remaining"].notna().any()
        and (pd.to_numeric(display["my_faab_remaining"], errors="coerce").fillna(0) > 0).any()
    )

    def _format_pct_of_faab(v):
        # Emoji prefix preserved in the rendered string; the underlying
        # sort uses the raw numeric column so "🔴 17.9%" still sorts above
        # "🟢 4.0%". Thresholds per Phase 1b plan: <5% green, 5-15% yellow,
        # >15% red.
        if v is None or pd.isna(v):
            return ""
        if v < 5:
            badge = "🟢"
        elif v < 15:
            badge = "🟡"
        else:
            badge = "🔴"
        return f"{badge} {v:.1f}%"

    if has_faab:
        display["pct_of_budget_display"] = display["high_bid_pct_of_faab"].apply(
            _format_pct_of_faab
        )
        if "bid_bucket" in display.columns:
            display["bid_bucket_display"] = display["bid_bucket"].apply(
                format_bid_bucket
            )
        else:
            display["bid_bucket_display"] = ""
    else:
        display["pct_of_budget_display"] = ""
        display["bid_bucket_display"] = ""

    # FTN status arrows live in `status_tag` (e.g. "⬆️", "⬇️"). Prefix the
    # player name when set so trending adds are scannable at a glance.
    def _prefix_arrow(row):
        name = row.get("player")
        tag = row.get("status_tag")
        if not isinstance(name, str):
            return name
        if not isinstance(tag, str) or not tag.strip():
            return name
        if tag in name:
            return name
        return f"{tag} {name}"

    display["player"] = display.apply(_prefix_arrow, axis=1)

    COLUMNS = {
        "player": "Player",
        "position": "Pos",
        "team": "Team",
        "ftn_type": "Type",
        "low_bid": "Low $",
        "high_bid": "High $",
        "theoretical_bid": "Theoretical $",
        "pct_of_budget_display": "% of Budget",
        "bid_bucket_display": "Bucket",
        "ros_value": "RoS $",
        "rfs12": "RFS12",
        "rfs15": "RFS15",
        "dollars": "Wk $",
        "dollars_per_game": "Wk $/G",
        "dollars_monday_thursday": "M-Th $",
        "dollars_friday_sunday": "F-Su $",
        "owner": "Owner",
        "own_pct": "Own%",
        "ftn_notes": "Notes",
    }

    # Hide FAAB-specific columns for draft-and-hold leagues (nolen_50 with
    # my_faab_remaining = 0). Everything else stays since projection/ROS
    # data is still useful for trade/drop decisions there.
    if not has_faab:
        COLUMNS.pop("pct_of_budget_display", None)
        COLUMNS.pop("bid_bucket_display", None)
        COLUMNS.pop("theoretical_bid", None)

    sort_cols = [
        c for c in ["has_ftn_rec", "high_bid", "ros_value"] if c in display.columns
    ]
    if sort_cols:
        display = display.sort_values(
            sort_cols, ascending=[False] * len(sort_cols), na_position="last"
        )

    visible = {k: v for k, v in COLUMNS.items() if k in display.columns}
    out = display[[c for c in visible if c in display.columns]].copy()

    for col in (
        "ros_value",
        "dollars",
        "dollars_per_game",
        "dollars_monday_thursday",
        "dollars_friday_sunday",
    ):
        if col in out.columns:
            out[col] = out[col].round(1)

    if "theoretical_bid" in out.columns:
        out["theoretical_bid"] = pd.to_numeric(
            out["theoretical_bid"], errors="coerce"
        ).round().astype("Int64")

    out = out.rename(columns=visible)

    worksheet_column_config = {}
    if "Theoretical $" in out.columns:
        worksheet_column_config["Theoretical $"] = st.column_config.NumberColumn(
            "Theoretical $",
            help=THEORETICAL_BID_HELP,
            format="$%d",
        )

    st.subheader(f"FAAB Worksheet — {selected_league}")

    if league_has_faab:
        with st.expander("Cross-league-size FTN recs (manual)", expanded=False):
            st.markdown(
                "FTN publishes separate 12- and 15-team FAAB files. A player can "
                "appear in one file and not the other. This table only shows the "
                "recommendation for **your** league’s FTN file size. "
                "If you are comparing to the **other** file, translate the low/high "
                "range using the role heuristics below (by FTN **Type**, not raw "
                "position). Round to a sensible whole-dollar bid."
            )
            st.markdown(
                "| Direction | Rule of thumb |\n"
                "|-----------|---------------|\n"
                "| **12T → 15T** (player only in the 12-team file) | Apply the "
                "multiplier to the **midpoint** of Low/High: **1.3×** default; "
                "**1.5×** closer / saves-chase specs; **1.4×** non-closer "
                "high-leverage RP; **1.25×** SP streamers. |\n"
                "| **15T → 12T** (player only in the 15-team file) | Divide the "
                "midpoint by the **same** factor (15-team pools are shallower; "
                "the name often clears for less). |\n"
            )
            st.caption(
                "Example: 12T Low/High 80–160 on a closer-type add — midpoint 120; "
                "for 15T context try ~1.5× → **~180** (illustrative only)."
            )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Players", len(out))
    c2.metric("FTN Recs", int((display["has_ftn_rec"] == 1).sum()))
    week_val = (
        display["week_of"].dropna().iloc[0]
        if "week_of" in display.columns and not display["week_of"].dropna().empty
        else "N/A"
    )
    c3.metric("Week Of", week_val)
    if has_faab:
        faab_val = pd.to_numeric(
            display["my_faab_remaining"], errors="coerce"
        ).dropna()
        faab_as_of = (
            display["faab_as_of_date"].dropna().iloc[0]
            if "faab_as_of_date" in display.columns
               and not display["faab_as_of_date"].dropna().empty
            else None
        )
        c4.metric(
            "Your FAAB",
            f"${int(faab_val.iloc[0])}" if not faab_val.empty else "N/A",
            help=(
                f"As of {faab_as_of}. Update `dbt/seeds/faab_remaining.csv` "
                "and re-seed weekly."
            ) if faab_as_of else None,
        )
    else:
        unowned_count = (
            len(display[display["owner"].isna() | (display["owner"] == "")])
            if "owner" in display.columns
            else "—"
        )
        c4.metric("Unowned", unowned_count)

    if league_has_faab:
        rem_src = df if "my_faab_remaining" in df.columns else display
        remaining_n = pd.to_numeric(rem_src["my_faab_remaining"], errors="coerce").dropna()
        as_of_n = None
        if "faab_as_of_date" in rem_src.columns and not rem_src["faab_as_of_date"].dropna().empty:
            as_of_n = rem_src["faab_as_of_date"].dropna().iloc[0]
        week_of_n = (
            rem_src["week_of"].dropna().iloc[0]
            if "week_of" in rem_src.columns and not rem_src["week_of"].dropna().empty
            else week_val
        )
        weeks_remaining_n = None
        try:
            plan_pace = load_weekly_category_plan(league_key)
            if (
                plan_pace is not None
                and not plan_pace.empty
                and "weeks_remaining" in plan_pace.columns
            ):
                wr = pd.to_numeric(plan_pace["weeks_remaining"], errors="coerce").dropna()
                if not wr.empty:
                    weeks_remaining_n = int(wr.iloc[0])
        except Exception:
            weeks_remaining_n = None
        season_start, periods = load_season_calendar()
        if weeks_remaining_n is None:
            week_date = parse_week_date(week_of_n, season_year=season_start.year)
            if week_date is not None:
                elapsed = season_week_number(week_date, season_start, periods)
                weeks_remaining_n = max(1, periods - elapsed)
        week_n = marker_week(
            as_of_value=as_of_n,
            week_of_value=week_of_n,
            season_start=season_start,
            periods=periods,
        )
        remaining_one = float(remaining_n.iloc[0]) if not remaining_n.empty else None
        snap = pacing_snapshot(
            remaining=remaining_one,
            weeks_remaining=weeks_remaining_n,
            week=week_n,
            as_of_date=None if as_of_n is None else str(as_of_n),
        )
        if snap is not None:
            pace_label = STATUS_LABELS.get(snap.status, snap.status)
            with st.expander(
                f"FAAB budget pacing — {pace_label} · ${snap.spent_to_date} spent",
                expanded=True,
                key=f"faab_pacing_expander_{league_key}",
            ):
                p1, p2, p3 = st.columns(3)
                p1.metric(
                    "Spent to date",
                    f"${snap.spent_to_date}",
                    help="starting_budget ($1,000) − my_faab_remaining. Not a weekly history.",
                )
                p2.metric(
                    "Pace",
                    pace_label,
                    help=(
                        f"Remaining ${snap.remaining} vs Elite ${snap.elite_remaining:.0f} "
                        f"/ 1st quartile ${snap.q1_remaining:.0f} implied remaining at week "
                        f"{snap.week}."
                    ),
                )
                p3.metric(
                    "$ / week left",
                    f"${snap.my_weekly_capacity:.0f}",
                    help=(
                        f"Remaining ÷ {snap.weeks_remaining} weeks left. Elite "
                        f"${snap.elite_weekly_capacity:.0f}/wk · 1st quartile "
                        f"${snap.q1_weekly_capacity:.0f}/wk."
                    ),
                )
                st.plotly_chart(
                    build_pacing_chart(snap),
                    use_container_width=True,
                    config={"displayModeBar": False},
                )
                st.caption(PACING_CAPTION)
                if snap.as_of_date:
                    st.caption(
                        f"Remaining as of {snap.as_of_date} (season week {snap.week}). "
                        "Update `dbt/seeds/faab_remaining.csv` after waivers."
                    )

    st.dataframe(
        out,
        use_container_width=True,
        hide_index=True,
        height=700,
        column_config=worksheet_column_config or None,
    )

    if has_faab:
        st.caption(
            "Bid bucket (*The Process* pp. 200–201): 🩹 triage = cheap gap-fill "
            "/ warm body; 🎯 tactical = short-term add; 🏆 strategic = "
            "difference-maker or contested market. FTN $ is price/heat, not "
            "quality — hyped prospects stay strategic even with weak RoS. "
            "Does not feed the lineup optimizer or FAAB what-if."
        )
        if "Theoretical $" in out.columns:
            st.caption(THEORETICAL_BID_CAPTION)

    if unmatched_count > 0:
        with st.expander(
            f"🟡 {unmatched_count} unmatched FTN players",
            expanded=st.session_state.get("open_unmatched_expander", False),
        ):
            st.markdown(
                "These FTN players could not be matched to an NFBC ID. "
                "Add overrides to `dbt/seeds/ftn_nfbc_player_overrides.csv` "
                "then run `dbt seed && dbt build`."
            )
            st.dataframe(unmatched_df, use_container_width=True, hide_index=True)

    # ------------------------------------------------------------------
    # FAAB what-if (#187)
    # ------------------------------------------------------------------
    st.markdown("---")
    st.subheader("FAAB what-if")
    st.caption(
        "Value a free-agent add by the change to your optimized week: Monday "
        "lock (Mon–Thu hitter $ + week pitchers) then Friday hitter re-optimize "
        "(Fri–Sun $) with pitchers locked. Filter free agents by position, then "
        "select one or more candidates. A bat started only Mon–Thu does not "
        "also get weekend volume — the Friday lineup’s stats are used instead. "
        "Uncertainty labels use the local noise floor from the weekly category "
        "plan when available."
    )

    try:
        wi_lineup = load_lineup_inputs(league_key)
        wi_slots = load_roster_slots()
    except Exception as e:
        st.warning(f"What-if unavailable (lineup inputs failed to load): {e}")
        wi_lineup = pd.DataFrame()
        wi_slots = pd.DataFrame()

    if wi_lineup.empty:
        st.info(
            "No `mart_weekly_lineup_inputs` rows for this league — what-if "
            "needs roster + free-agent projections."
        )
    else:
        wi_owners = sorted(
            wi_lineup["owner"]
            .dropna()
            .loc[wi_lineup["owner"] != ""]
            .unique()
            .tolist()
        )
        wi_owner = st.selectbox(
            "Your team (roster to optimize)",
            wi_owners,
            index=default_index(wi_owners),
            key=f"faab_whatif_owner_{league_key}",
        )

        fa_mask = wi_lineup["owner"].isna() | (wi_lineup["owner"] == "")
        free_agents_df = wi_lineup.loc[fa_mask].copy()
        roster_df = wi_lineup.loc[wi_lineup["owner"] == wi_owner].copy()

        def _parse_pos_wi(raw):
            if raw is None:
                return []
            return [p.strip().upper() for p in str(raw).split(",") if p.strip()]

        for frame in (roster_df, free_agents_df):
            if frame.empty:
                continue
            if "row_type" not in frame.columns:
                frame["row_type"] = "hitter"
            frame["row_type"] = frame["row_type"].fillna("hitter")
            if "pos_raw" in frame.columns:
                frame["pos_array"] = frame["pos_raw"].apply(_parse_pos_wi)

        fmt = (
            wi_lineup["format"].dropna().iloc[0]
            if "format" in wi_lineup.columns
            and not wi_lineup["format"].dropna().empty
            else None
        )
        hitter_slots = {}
        pitcher_slots = {}
        if fmt is not None and not wi_slots.empty:
            hs = wi_slots[
                (wi_slots["format"] == fmt) & (wi_slots["slot_group"] == "hitter")
            ]
            ps = wi_slots[
                (wi_slots["format"] == fmt) & (wi_slots["slot_group"] == "pitcher")
            ]
            hitter_slots = dict(
                zip(hs["slot"].astype(str), hs["count"].astype(int))
            )
            pitcher_slots = dict(
                zip(ps["slot"].astype(str), ps["count"].astype(int))
            )
        slot_counts_wi = {**hitter_slots, **pitcher_slots}

        plan_rows_wi = None
        try:
            plan_df_wi = load_weekly_category_plan(league_key)
            if not plan_df_wi.empty and "team_name" in plan_df_wi.columns:
                team_opts = sorted(
                    plan_df_wi["team_name"].dropna().unique().tolist()
                )
                preferred = [t for t in team_opts if t == wi_owner]
                plan_team = st.selectbox(
                    "Overall standings team (pts/unit + noise floor)",
                    team_opts,
                    index=team_opts.index(preferred[0]) if preferred else 0,
                    key="faab_whatif_plan_team",
                )
                plan_rows_wi = plan_df_wi.loc[
                    plan_df_wi["team_name"] == plan_team
                ].to_dict(orient="records")
            else:
                st.caption(
                    "No weekly category plan for this league (stand-alone or "
                    "not built). Raw category deltas still show; team-fit "
                    "ranking falls back to weekly $."
                )
        except Exception:
            st.caption(
                "Weekly category plan unavailable — raw deltas only; "
                "ranking uses weekly $."
            )

        if roster_df.empty:
            st.warning(f"No rostered players for `{wi_owner}`.")
        elif not slot_counts_wi:
            st.warning("No roster slot config for this format.")
        else:
            roster_players = roster_df.to_dict(orient="records")
            fa_players = free_agents_df.to_dict(orient="records")

            fa_labels = {}
            for p in fa_players:
                nid = p.get("nfbc_id")
                if nid is None or (isinstance(nid, float) and nid != nid):
                    continue
                name = p.get("player_name") or str(nid)
                pos = p.get("pos_raw") or ""
                dol = p.get("dollars_monday_thursday")
                if dol is None or (isinstance(dol, float) and dol != dol):
                    dol = p.get("dollars")
                try:
                    dol_s = f"${float(dol):.1f}" if dol is not None else "—"
                except (TypeError, ValueError):
                    dol_s = "—"
                fa_labels[str(int(float(nid))) if str(nid).replace(".", "", 1).isdigit() else str(nid)] = (
                    f"{name} ({pos}) · {dol_s}"
                )

            # Normalize keys to match candidate id types from multiselect
            fa_by_id = {}
            for p in fa_players:
                nid = p.get("nfbc_id")
                if nid is None or (isinstance(nid, float) and nid != nid):
                    continue
                fa_by_id[str(nid)] = p
                try:
                    fa_by_id[str(int(float(nid)))] = p
                except (TypeError, ValueError):
                    pass

            owned_labels = {}
            for p in roster_players:
                nid = p.get("nfbc_id")
                if nid is None:
                    continue
                rt = p.get("row_type") or "hitter"
                name = p.get("player_name") or str(nid)
                owned_labels[f"{nid}|{rt}"] = f"{name} ({rt})"

            # Narrow the FA pool by position before picking candidates.
            fa_pos_tokens = sorted(
                {
                    t
                    for p in fa_players
                    for t in (p.get("pos_array") or [])
                    if t
                }
            )
            fa_pos_filter = st.multiselect(
                "Filter free agents by position",
                options=fa_pos_tokens,
                default=[],
                key="faab_whatif_fa_pos",
                help=(
                    "Leave empty to list all free agents. Select one or more "
                    "positions (e.g. SS, OF, SP) to narrow the Add candidate "
                    "list — a player matches if any of their eligibilities "
                    "is selected."
                ),
            )

            def _fa_matches_pos_filter(player):
                if not fa_pos_filter:
                    return True
                if player is None:
                    return False
                tokens = player.get("pos_array") or []
                return any(t in fa_pos_filter for t in tokens)

            filtered_add_options = [
                k
                for k in fa_labels
                if _fa_matches_pos_filter(fa_by_id.get(k))
            ]
            filtered_add_options = sorted(
                filtered_add_options, key=lambda k: fa_labels[k]
            )
            st.caption(
                f"Showing **{len(filtered_add_options)}** of "
                f"**{len(fa_labels)}** free agents"
                + (
                    f" (positions: {', '.join(fa_pos_filter)})"
                    if fa_pos_filter
                    else " (all positions)"
                )
                + "."
            )

            c_add, c_drop, c_mode = st.columns([2, 2, 1])
            with c_add:
                selected_adds = st.multiselect(
                    "Add candidate(s)",
                    options=filtered_add_options,
                    format_func=lambda k: fa_labels.get(k, k),
                    key="faab_whatif_adds",
                )
            with c_drop:
                drop_mode = st.radio(
                    "Drop",
                    options=["auto", "explicit"],
                    format_func=lambda m: (
                        "Suggested (coverage-aware)"
                        if m == "auto"
                        else "Explicit drop"
                    ),
                    horizontal=True,
                    key="faab_whatif_drop_mode",
                )
                explicit_drop = None
                if drop_mode == "explicit" and owned_labels:
                    explicit_drop = st.selectbox(
                        "Drop player",
                        options=list(owned_labels.keys()),
                        format_func=lambda k: owned_labels.get(k, k),
                        key="faab_whatif_drop",
                    )
            with c_mode:
                rank_mode = st.radio(
                    "Rank by",
                    options=[RANK_MODE_OVERALL, RANK_MODE_WEEKLY],
                    format_func=lambda m: (
                        "Team-fit pts"
                        if m == RANK_MODE_OVERALL
                        else "Weekly $"
                    ),
                    key="faab_whatif_rank_mode",
                )

            if not fa_labels:
                st.info("No free agents in weekly lineup inputs for this league.")
            elif fa_pos_filter and not filtered_add_options:
                st.info(
                    "No free agents match the selected position(s). "
                    "Clear or widen the position filter."
                )
            elif not selected_adds:
                st.info(
                    "Select at least one free-agent candidate"
                    + (
                        " (from the filtered list)."
                        if fa_pos_filter
                        else "."
                    )
                )
            else:
                drop_key = None
                auto_suggest = drop_mode == "auto"
                if drop_mode == "explicit" and explicit_drop:
                    nid_s, rt = explicit_drop.split("|", 1)
                    try:
                        nid_val = int(float(nid_s))
                    except (TypeError, ValueError):
                        nid_val = nid_s
                    drop_key = (nid_val, rt)

                ranked = rank_candidates(
                    roster_players,
                    slot_counts_wi,
                    selected_adds,
                    free_agents=fa_players,
                    drop_key=drop_key,
                    auto_suggest_drop=auto_suggest,
                    rank_mode=rank_mode,
                    plan_rows=plan_rows_wi,
                    mode="monday",
                )
                warn = ranked[0].get("_unmatched_warning") if ranked else None
                if warn:
                    st.warning(warn)

                def _projected_cell(row):
                    pid = row.get("add_nfbc_id")
                    player = fa_by_id.get(str(pid)) if pid is not None else None
                    if player is None and pid is not None:
                        try:
                            player = fa_by_id.get(str(int(float(pid))))
                        except (TypeError, ValueError):
                            player = None
                    line = format_projected_stats(player)
                    if line:
                        return line
                    return row.get("projected_stats") or ""

                rank_view = pd.DataFrame(
                    [
                        {
                            "Rank": (
                                f"T{r['display_rank']}"
                                if r.get("tied")
                                else r.get("display_rank")
                            ),
                            "Add": fa_labels.get(
                                str(r["add_nfbc_id"]), str(r["add_nfbc_id"])
                            ),
                            "Projected": _projected_cell(r),
                            "Drop": r.get("drop_nfbc_id"),
                            "Δ weekly $": r.get("net_weekly_value"),
                            "Δ overall pts (est.)": r.get(
                                "net_overall_pts_estimate"
                            ),
                            "Tied": "yes" if r.get("tied") else "",
                            "Bench-only": (
                                "yes" if r.get("bench_only_add") else ""
                            ),
                            "OK": r.get("ok"),
                            "Note": r.get("message"),
                        }
                        for r in ranked
                    ]
                )
                if "Projected" in rank_view.columns:
                    rank_view["Projected"] = [
                        "" if v is None else str(v)
                        for v in rank_view["Projected"].tolist()
                    ]
                st.markdown("#### Candidate ranking")
                st.dataframe(
                    rank_view,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Projected": st.column_config.TextColumn(
                            "Projected",
                            width="large",
                            help=(
                                "Weekly Razzball line from lineup inputs: "
                                "R/HR/RBI/SB/AVG for hitters, "
                                "GS/IP/W/SV/K/ERA/WHIP for pitchers."
                            ),
                        ),
                    },
                )
                st.caption(
                    "Projected is the weekly Razzball line: R/HR/RBI/SB/AVG "
                    "for hitters, GS/IP/W/SV/K/ERA/WHIP for pitchers. "
                    "Candidates whose team-fit deltas differ by less than the "
                    "local noise floor share a tie rank (T#). The optimizer "
                    "itself stays deterministic."
                )

                inspect_ids = [
                    str(r["add_nfbc_id"]) for r in ranked if r.get("ok")
                ]
                if inspect_ids:
                    focus = st.selectbox(
                        "Inspect add/drop lineups",
                        options=inspect_ids,
                        format_func=lambda k: fa_labels.get(k, k),
                        key="faab_whatif_inspect",
                    )
                    detail = analyze_add_drop(
                        roster_players,
                        slot_counts_wi,
                        add_nfbc_id=focus,
                        free_agents=fa_players,
                        drop_key=drop_key,
                        auto_suggest_drop=auto_suggest,
                        plan_rows=plan_rows_wi,
                        mode="monday",
                    )
                    if not detail.ok:
                        st.error(detail.message)
                    else:
                        st.success(detail.message)
                        m1, m2, m3 = st.columns(3)
                        m1.metric(
                            "Δ weekly $", f"{detail.net_weekly_value:+.1f}"
                        )
                        if detail.net_overall_pts_estimate is not None:
                            m2.metric(
                                "Δ overall pts (est.)",
                                f"{detail.net_overall_pts_estimate:+.1f}",
                                help=(
                                    "Point estimate — see uncertainty column. "
                                    "Moves inside the noise floor are labeled "
                                    "within_noise."
                                ),
                            )
                        else:
                            m2.metric("Δ overall pts (est.)", "—")
                        m3.metric(
                            "Noise flags",
                            (
                                "some within noise"
                                if detail.any_within_noise
                                else "clear / n/a"
                            ),
                        )

                        delta_df = pd.DataFrame(format_delta_rows(detail.category_deltas))
                        if not delta_df.empty:
                            st.markdown("#### Category deltas")
                            show = delta_df.rename(
                                columns={
                                    "category": "Cat",
                                    "baseline": "Baseline",
                                    "what_if": "What-if",
                                    "delta_raw": "Δ raw",
                                    "pts_per_unit": "Pts/unit",
                                    "delta_overall_pts_est": "Δ overall pts",
                                    "noise_floor": "Noise floor",
                                    "uncertainty": "Uncertainty",
                                }
                            )
                            st.dataframe(
                                show.drop(columns=["is_ratio"], errors="ignore"),
                                use_container_width=True,
                                hide_index=True,
                            )

                        left, right = st.columns(2)
                        with left:
                            st.markdown("#### Baseline — Monday lock")
                            st.dataframe(
                                pd.DataFrame(
                                    starters_table(
                                        detail.baseline,
                                        dollar_field="dollars_monday_thursday",
                                    )
                                ),
                                use_container_width=True,
                                hide_index=True,
                            )
                        with right:
                            st.markdown("#### What-if — Monday lock")
                            st.dataframe(
                                pd.DataFrame(
                                    starters_table(
                                        detail.what_if,
                                        dollar_field="dollars_monday_thursday",
                                    )
                                ),
                                use_container_width=True,
                                hide_index=True,
                            )

                        if detail.baseline_friday is not None and detail.what_if_friday is not None:
                            l2, r2 = st.columns(2)
                            with l2:
                                st.markdown("#### Baseline — Friday swap")
                                st.dataframe(
                                    pd.DataFrame(
                                        starters_table(
                                            detail.baseline_friday,
                                            dollar_field="dollars_friday_sunday",
                                        )
                                    ),
                                    use_container_width=True,
                                    hide_index=True,
                                )
                            with r2:
                                st.markdown("#### What-if — Friday swap")
                                st.dataframe(
                                    pd.DataFrame(
                                        starters_table(
                                            detail.what_if_friday,
                                            dollar_field="dollars_friday_sunday",
                                        )
                                    ),
                                    use_container_width=True,
                                    hide_index=True,
                                )
                            st.caption(
                                "Pitchers are locked from Monday. Friday tables "
                                "show hitter re-optimization on Fri–Sun $."
                            )


# ---------------------------------------------------------------------------
# Lineup Optimizer tab (Phase 1a v1: greedy, Monday-lock, hitters only)
# ---------------------------------------------------------------------------

with tab_lineup:
    st.subheader(f"Weekly Lineup Optimizer — {selected_league}")

    try:
        lineup_df = load_lineup_inputs(league_key)
        slots_df = load_roster_slots()
    except Exception as e:
        st.error(f"Failed to load lineup data: {e}")
        st.stop()

    if lineup_df.empty:
        st.warning(
            f"No rows in `mart_weekly_lineup_inputs` for league `{league_key}`. "
            "Confirm the in-season-players CSV is uploaded to S3 for today's "
            "partition and `dbt build` has run."
        )
        st.stop()

    owner_options = sorted(
        lineup_df["owner"].dropna().loc[lineup_df["owner"] != ""].unique().tolist()
    )
    if not owner_options:
        st.warning("No owners found in the lineup inputs mart.")
        st.stop()

    selected_owner = st.selectbox(
        "Owner (team to optimize)",
        owner_options,
        index=default_index(owner_options),
        key=f"lineup_owner_{league_key}",
    )

    fmt = lineup_df["format"].dropna().iloc[0]
    week_of = (
        lineup_df["week_of"].dropna().iloc[0]
        if "week_of" in lineup_df.columns and not lineup_df["week_of"].dropna().empty
        else "N/A"
    )

    def _slot_counts_for(group):
        rows = slots_df[
            (slots_df["format"] == fmt) & (slots_df["slot_group"] == group)
        ]
        return dict(
            zip(
                rows["slot"].astype(str).tolist(),
                rows["count"].astype(int).tolist(),
            )
        )

    hitter_slot_counts = _slot_counts_for("hitter")
    pitcher_slot_counts = _slot_counts_for("pitcher")

    if not hitter_slot_counts:
        st.error(
            f"No hitter slot config found for format `{fmt}` in "
            "`league_roster_slots`. Re-run `dbt seed`."
        )
        st.stop()

    # NFBC locks pitchers for the full week on Monday and allows hitter-only
    # swaps on Friday, so the two windows are different questions.
    lineup_mode = st.radio(
        "Lineup window",
        options=["monday", "friday"],
        format_func=lambda m: (
            "Monday lock (Mon–Thu hitters + week pitchers)"
            if m == "monday"
            else "Friday swap (hitters only, Fri–Sun)"
        ),
        horizontal=True,
        key="lineup_mode",
    )

    # Objective: Neutral $ (default) vs Team-fit overall-pts weights (#218).
    plan_df_lineup = pd.DataFrame()
    try:
        plan_df_lineup = load_weekly_category_plan(league_key)
    except Exception:
        plan_df_lineup = pd.DataFrame()

    team_options_plan = []
    if not plan_df_lineup.empty and "team_name" in plan_df_lineup.columns:
        team_options_plan = sorted(
            plan_df_lineup["team_name"].dropna().unique().tolist()
        )

    objective = OBJECTIVE_NEUTRAL
    weights = None
    ratio_context = None
    plan_team = None

    if not team_options_plan:
        st.caption(
            "Team-fit (overall pts) is unavailable — no weekly category plan "
            "/ mobility rows for this league (stand-alone or not built). "
            "Neutral Razzball `$` scoring is used."
        )
    else:
        obj_choice = st.radio(
            "Scoring objective",
            options=[OBJECTIVE_NEUTRAL, OBJECTIVE_TEAM_FIT],
            format_func=lambda m: (
                "Neutral (Razzball $)"
                if m == OBJECTIVE_NEUTRAL
                else "Team-fit (overall pts / mobility)"
            ),
            horizontal=True,
            key="lineup_objective",
            help=(
                "Neutral maximizes period `$`. Team-fit weights each raw "
                "projection unit by overall_points_per_raw_unit for the "
                "selected standings team."
            ),
        )
        preferred_plan = [t for t in team_options_plan if t == selected_owner]
        if preferred_plan:
            plan_idx = team_options_plan.index(preferred_plan[0])
        else:
            plan_idx = default_index(team_options_plan)
        plan_team = st.selectbox(
            "Overall standings team (for Team-fit weights)",
            team_options_plan,
            index=plan_idx,
            key=f"lineup_plan_team_{league_key}",
            disabled=(obj_choice != OBJECTIVE_TEAM_FIT),
        )
        if obj_choice == OBJECTIVE_TEAM_FIT:
            plan_rows = plan_df_lineup.loc[
                plan_df_lineup["team_name"] == plan_team
            ].to_dict(orient="records")
            weights = weights_from_plan_rows(plan_rows)
            ratio_context = ratio_context_from_plan_rows(plan_rows)
            ready, ready_msg = team_fit_inputs_ready(weights, ratio_context)
            if not ready:
                st.warning(
                    f"Team-fit unavailable for `{plan_team}`: {ready_msg} "
                    "Falling back to Neutral `$`."
                )
                weights = None
                ratio_context = None
                objective = OBJECTIVE_NEUTRAL
            else:
                objective = OBJECTIVE_TEAM_FIT
                with st.expander("Team-fit weights (pts per raw unit)", expanded=False):
                    wrows = [
                        {"category": k.upper(), "pts / unit": v}
                        for k, v in sorted(weights.items())
                    ]
                    st.dataframe(
                        pd.DataFrame(wrows),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.caption(
                        f"Ratio context: hits={ratio_context.get('hits')}, "
                        f"AB={ratio_context.get('at_bats')}, "
                        f"ER={ratio_context.get('earned_runs')}, "
                        f"IP={ratio_context.get('innings_pitched')}, "
                        f"BB+H={ratio_context.get('walks_hits_allowed')}."
                    )

    slot_counts = dict(hitter_slot_counts)
    if lineup_mode == "monday":
        slot_counts.update(pitcher_slot_counts)

    team_all = lineup_df[lineup_df["owner"] == selected_owner].copy()

    if team_all.empty:
        st.warning(f"No players rostered to `{selected_owner}`.")
        st.stop()

    if "row_type" not in team_all.columns:
        team_all["row_type"] = "hitter"
    team_all["row_type"] = team_all["row_type"].fillna("hitter")

    # Derive pos_array from pos_raw (plain comma-separated string) rather
    # than trusting the Athena array column — pyathena serializes arrays as
    # strings like "[C, 1B]" which breaks naive comma splits.
    def _parse_pos(raw):
        if raw is None:
            return []
        return [p.strip().upper() for p in str(raw).split(",") if p.strip()]

    team_all["pos_array"] = team_all["pos_raw"].apply(_parse_pos)

    team = team_all[team_all["row_type"] == "hitter"].copy()
    team_pitchers = team_all[team_all["row_type"] == "pitcher"].copy()

    if team.empty:
        st.warning(
            f"No hitter rows rostered to `{selected_owner}` with a weekly "
            "hitting projection."
        )
        st.stop()

    players = team_all.to_dict(orient="records")
    opt_kwargs = {}
    if objective == OBJECTIVE_TEAM_FIT and weights is not None:
        opt_kwargs["weights"] = weights
        opt_kwargs["ratio_context"] = ratio_context

    if lineup_mode == "friday":
        # Pitchers were locked Monday; carry the Monday set through untouched.
        # Use the same objective so Team-fit Monday pitchers stay locked.
        monday = optimize_week(
            players,
            {**hitter_slot_counts, **pitcher_slot_counts},
            mode="monday",
            **opt_kwargs,
        )
        locked_pitchers = [
            a.player for a in monday.starters if a.slot == "P"
        ]
        result = optimize_week(
            players,
            slot_counts,
            mode="friday",
            locked_pitchers=locked_pitchers,
            **opt_kwargs,
        )
    else:
        result = optimize_week(
            players, slot_counts, mode="monday", **opt_kwargs
        )

    active_capacity = sum(slot_counts.values())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Week Of", str(week_of))
    c2.metric("Team Hitters", len(team))
    c3.metric("Active Slots", active_capacity)
    if objective == OBJECTIVE_TEAM_FIT:
        c4.metric(
            "Team-fit score",
            f"{result.total_score:.1f}",
            help=(
                "Sum of overall-points contributions from the active "
                f"starters for standings team `{plan_team}`. Not Razzball $."
            ),
        )
    else:
        c4.metric("Projected $", f"{result.total_score:.1f}")

    if objective == OBJECTIVE_TEAM_FIT:
        st.info(
            f"**Objective: Team-fit (overall pts)** for `{plan_team}`. "
            "Starters maximize mobility-weighted category contribution, not "
            "Razzball `$`. Toggle Scoring objective to Neutral to compare."
        )
    else:
        st.caption(
            "**Objective: Neutral (Razzball $)** — Monday uses Mon–Thu hitter "
            "$; Friday uses Fri–Sun hitter $; pitchers use full-week $."
        )

    # Half-week display: Mon–Thu ``mt_*`` / Fri–Sun ``fs_*`` when present.
    use_mt = lineup_mode == "monday"
    use_fs = lineup_mode == "friday"
    if lineup_mode == "monday":
        dollar_field, dollar_label = "dollars_monday_thursday", "M-Th $"
    elif lineup_mode == "friday":
        dollar_field, dollar_label = "dollars_friday_sunday", "F-Su $"
    else:
        dollar_field, dollar_label = "dollars", "Wk $"

    _HITTER_HALF_FIELDS = {
        "r": ("mt_r", "fs_r"),
        "hr": ("mt_hr", "fs_hr"),
        "rbi": ("mt_rbi", "fs_rbi"),
        "sb": ("mt_sb", "fs_sb"),
        "hits": ("mt_hits", "fs_hits"),
        "ab": ("mt_ab", "fs_ab"),
        "num_g": ("mt_num_g", "fs_num_g"),
        "home_games": ("mt_home_games", "fs_home_games"),
        "away_games": ("mt_away_games", "fs_away_games"),
        "vs_rhp": ("mt_vs_rhp", "fs_vs_rhp"),
        "vs_lhp": ("mt_vs_lhp", "fs_vs_lhp"),
        "batting_avg": ("mt_batting_avg", "fs_batting_avg"),
    }

    def _num(value):
        try:
            if value is None or (isinstance(value, float) and value != value):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _hitter_stat(player, key):
        """Prefer ``mt_*`` / ``fs_*`` on Monday / Friday; else full-week."""
        half = _HITTER_HALF_FIELDS.get(key)
        if half is not None:
            period_key = half[0] if use_mt else (half[1] if use_fs else None)
            if period_key is not None:
                period_val = _num(player.get(period_key))
                if period_val is not None:
                    return period_val
        return _num(player.get(key))

    def _fmt(value, spec):
        return format(value, spec) if isinstance(value, (int, float)) else "—"

    # Expected totals from starters only — two cross-tabs (hitting / pitching).
    hit_r = hit_hr = hit_rbi = hit_sb = hit_h = hit_ab = 0.0
    pit_k = pit_w = pit_sv = pit_ip = pit_er = pit_h = pit_bb = 0.0
    for a in result.starters:
        p = a.player
        if a.slot == "P" or (p.get("row_type") or "hitter") == "pitcher":
            pit_k += _num(p.get("k")) or 0.0
            pit_w += _num(p.get("w")) or 0.0
            pit_sv += _num(p.get("sv")) or 0.0
            pit_ip += _num(p.get("ip")) or 0.0
            pit_er += _num(p.get("er")) or 0.0
            pit_h += _num(p.get("hits_allowed")) or 0.0
            pit_bb += _num(p.get("walks_allowed")) or 0.0
        else:
            hit_r += _hitter_stat(p, "r") or 0.0
            hit_hr += _hitter_stat(p, "hr") or 0.0
            hit_rbi += _hitter_stat(p, "rbi") or 0.0
            hit_sb += _hitter_stat(p, "sb") or 0.0
            hit_h += _hitter_stat(p, "hits") or 0.0
            hit_ab += _hitter_stat(p, "ab") or 0.0

    hit_avg = (hit_h / hit_ab) if hit_ab > 0 else None
    pit_era = ((pit_er * 9.0) / pit_ip) if pit_ip > 0 else None
    pit_whip = ((pit_h + pit_bb) / pit_ip) if pit_ip > 0 else None

    window_note = (
        "Mon–Thu hitter projections"
        if use_mt
        else (
            "Fri–Sun hitter projections"
            if use_fs
            else "full-week projections"
        )
    )
    st.markdown("### Expected lineup totals")
    st.caption(
        f"Starters only ({window_note}). Ratios use summed numerators/"
        "denominators (H÷AB, ER×9÷IP, (H+BB)÷IP), not averaged player rates."
    )
    hit_col, pit_col = st.columns(2)
    with hit_col:
        st.markdown("**Hitting**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "R": _fmt(hit_r, ".1f"),
                        "HR": _fmt(hit_hr, ".1f"),
                        "RBI": _fmt(hit_rbi, ".1f"),
                        "SB": _fmt(hit_sb, ".1f"),
                        "H": _fmt(hit_h, ".1f"),
                        "AB": _fmt(hit_ab, ".1f"),
                        "AVG": _fmt(hit_avg, ".3f"),
                    }
                ],
                index=["Projected"],
            ),
            use_container_width=True,
        )
    with pit_col:
        st.markdown("**Pitching**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "K": _fmt(pit_k, ".1f"),
                        "W": _fmt(pit_w, ".1f"),
                        "SV": _fmt(pit_sv, ".1f"),
                        "IP": _fmt(pit_ip, ".1f"),
                        "ER": _fmt(pit_er, ".1f"),
                        "H": _fmt(pit_h, ".1f"),
                        "BB": _fmt(pit_bb, ".1f"),
                        "ERA": _fmt(pit_era, ".2f"),
                        "WHIP": _fmt(pit_whip, ".2f"),
                    }
                ],
                index=["Projected"],
            ),
            use_container_width=True,
        )

    if result.missing_projection_ids:
        st.info(
            f"{len(result.missing_projection_ids)} rostered player(s) have no "
            "weekly projection for this window and were scored as zero."
        )

    if result.unfilled_slots:
        st.warning(
            "Unfilled slots (not enough eligible hitters): "
            + ", ".join(result.unfilled_slots)
        )

    HITTER_COLS = [
        "slot",
        "player_name",
        "team",
        "pos_raw",
        "bats",
        "num_g",
        "dollars",
        "r",
        "hr",
        "rbi",
        "sb",
        "hits",
        "ab",
        "batting_avg",
        "home_games",
        "away_games",
        "vs_rhp",
        "vs_lhp",
        "ros_value",
        "rate_flag",
    ]
    HITTER_LABELS = {
        "slot": "Slot",
        "player_name": "Player",
        "team": "Team",
        "pos_raw": "Pos",
        "bats": "B",
        "num_g": "G",
        "dollars": dollar_label,
        "r": "R",
        "hr": "HR",
        "rbi": "RBI",
        "sb": "SB",
        "hits": "H",
        "ab": "AB",
        "batting_avg": "AVG",
        "home_games": "HG",
        "away_games": "AG",
        "vs_rhp": "vR",
        "vs_lhp": "vL",
        "ros_value": "RoS $",
        "rate_flag": "Rate flags",
    }

    divergence_df = pd.DataFrame()
    try:
        divergence_df = load_projection_divergence()
    except Exception:
        divergence_df = pd.DataFrame()
    flag_by_id = summarize_player_flags(
        divergence_df,
        projection_slices=["weekly", "weekend", "monday_thursday"],
    )
    if flag_by_id:
        st.caption(flag_caption())

    def _hitter_row(player, *, slot=None):
        row = {}
        if slot is not None:
            row["slot"] = slot
        row["player_name"] = player.get("player_name")
        row["team"] = player.get("team")
        row["pos_raw"] = player.get("pos_raw")
        row["bats"] = player.get("bats")
        row["num_g"] = _hitter_stat(player, "num_g")
        dollar_val = _num(player.get(dollar_field))
        if dollar_val is None:
            dollar_val = _num(player.get("dollars"))
        row["dollars"] = dollar_val
        for key in ("r", "hr", "rbi", "sb", "hits", "ab", "batting_avg"):
            row[key] = _hitter_stat(player, key)
        if row["batting_avg"] is None and row["ab"]:
            hits = row["hits"] or 0.0
            row["batting_avg"] = hits / row["ab"] if row["ab"] else None
        row["home_games"] = _hitter_stat(player, "home_games")
        row["away_games"] = _hitter_stat(player, "away_games")
        row["vs_rhp"] = _hitter_stat(player, "vs_rhp")
        row["vs_lhp"] = _hitter_stat(player, "vs_lhp")
        row["ros_value"] = _num(player.get("ros_value"))
        nid = player.get("nfbc_id")
        row["rate_flag"] = flag_by_id.get(nid, "")
        if not row["rate_flag"]:
            try:
                row["rate_flag"] = flag_by_id.get(int(nid), "")
            except (TypeError, ValueError):
                pass
        return row

    def _hitter_frame(records, *, include_slot=False):
        cols = HITTER_COLS if include_slot else [c for c in HITTER_COLS if c != "slot"]
        df_h = pd.DataFrame(records, columns=cols)
        for col in cols:
            if col in ("slot", "player_name", "team", "pos_raw", "bats", "rate_flag"):
                continue
            decimals = 3 if col == "batting_avg" else (1 if col in ("dollars", "ros_value", "sb", "r", "hr", "rbi", "hits", "ab") else 1)
            df_h[col] = pd.to_numeric(df_h[col], errors="coerce").round(decimals)
        return df_h

    starters_records = []
    slot_order_index = {s: i for i, s in enumerate(SLOT_DISPLAY_ORDER)}
    for a in result.starters:
        if a.slot == "P":
            continue
        starters_records.append(_hitter_row(a.player, slot=a.slot))

    starters_df = _hitter_frame(starters_records, include_slot=True)
    if not starters_df.empty:
        starters_df["_slot_order"] = starters_df["slot"].map(slot_order_index).fillna(99)
        starters_df = (
            starters_df.sort_values(["_slot_order", "dollars"], ascending=[True, False])
            .drop(columns=["_slot_order"])
            .reset_index(drop=True)
        )

    st.markdown("### Starters")
    st.dataframe(
        starters_df.rename(columns=HITTER_LABELS),
        use_container_width=True,
        hide_index=True,
    )

    bench_hitters = [
        p for p in result.bench if (p.get("row_type") or "hitter") == "hitter"
    ]
    bench_pitchers = [p for p in result.bench if p.get("row_type") == "pitcher"]
    bench_records = [_hitter_row(p) for p in bench_hitters]
    bench_df = _hitter_frame(bench_records, include_slot=False)
    if not bench_df.empty:
        bench_df = bench_df.sort_values(
            "dollars", ascending=False, na_position="last"
        ).reset_index(drop=True)

    st.markdown(f"### Bench hitters ({len(bench_df)})")
    st.dataframe(
        bench_df.rename(columns=HITTER_LABELS),
        use_container_width=True,
        hide_index=True,
    )

    PITCHER_COLS = [
        "player_name",
        "team",
        "pos_raw",
        "dollars",
        "pitch_g",
        "gs",
        "first_start_day",
        "is_two_start",
        "opps",
        "w",
        "sv",
        "k",
        "ip",
        "er",
        "hits_allowed",
        "walks_allowed",
        "era",
        "whip",
        "ros_value",
        "rate_flag",
    ]
    PITCHER_LABELS = {
        "player_name": "Player",
        "team": "Team",
        "pos_raw": "Pos",
        "dollars": "Wk $",
        "pitch_g": "G",
        "gs": "GS",
        "first_start_day": "1st",
        "is_two_start": "2-start",
        "opps": "Opp",
        "w": "W",
        "sv": "SV",
        "k": "K",
        "ip": "IP",
        "er": "ER",
        "hits_allowed": "H",
        "walks_allowed": "BB",
        "era": "ERA",
        "whip": "WHIP",
        "ros_value": "RoS $",
        "rate_flag": "Rate flags",
    }

    def _pitcher_frame(records):
        enriched = []
        for p in records:
            row = {k: p.get(k) for k in PITCHER_COLS if k != "rate_flag"}
            nid = p.get("nfbc_id")
            flag = flag_by_id.get(nid, "")
            if not flag:
                try:
                    flag = flag_by_id.get(int(nid), "")
                except (TypeError, ValueError):
                    flag = ""
            row["rate_flag"] = flag
            enriched.append(row)
        df_p = pd.DataFrame(enriched, columns=PITCHER_COLS)
        for col in PITCHER_COLS:
            if col in (
                "player_name",
                "team",
                "pos_raw",
                "first_start_day",
                "opps",
                "is_two_start",
                "rate_flag",
            ):
                continue
            df_p[col] = pd.to_numeric(df_p[col], errors="coerce").round(2)
        return df_p.sort_values("dollars", ascending=False, na_position="last")

    started_pitchers = [a.player for a in result.starters if a.slot == "P"]

    if started_pitchers:
        st.markdown(f"### Pitchers started ({len(started_pitchers)})")
        if lineup_mode == "friday":
            st.caption(
                "Pitchers locked at Monday's deadline — NFBC does not allow "
                "pitcher changes during the week."
            )
        st.dataframe(
            _pitcher_frame(started_pitchers).rename(columns=PITCHER_LABELS),
            use_container_width=True,
            hide_index=True,
        )

    if bench_pitchers:
        st.markdown(f"### Bench pitchers ({len(bench_pitchers)})")
        st.dataframe(
            _pitcher_frame(bench_pitchers).rename(columns=PITCHER_LABELS),
            use_container_width=True,
            hide_index=True,
        )

    # ------------------------------------------------------------------
    # Two-Start Pitchers (#59) — my roster + free agents
    # ------------------------------------------------------------------
    st.markdown("### Two-Start Pitchers")
    st.caption(schedule_bucket_caption())

    faab_for_two_start = pd.DataFrame()
    try:
        faab_for_two_start = load_faab_data(league_key)
    except Exception:
        faab_for_two_start = pd.DataFrame()

    two_start_df = build_two_start_rows(
        lineup_df,
        selected_owner=selected_owner,
        faab_df=faab_for_two_start,
    )
    if two_start_df.empty:
        st.info(
            "No two-start pitchers on your roster or the free-agent pool for "
            "this week (or `is_two_start` is not populated yet)."
        )
    else:
        two_start_view = pd.DataFrame(
            {
                "Status": two_start_df["status"],
                "Player": two_start_df["player_name"],
                "Team": two_start_df["team"],
                "Pos": two_start_df["pos_raw"],
                "Wk $": two_start_df["weekly_projection_value"],
                "1st": two_start_df["first_start_day"],
                "Team G": two_start_df["team_games"],
                "Schedule": two_start_df["schedule_bucket"],
                "Opp": two_start_df["opps"],
                "Own %": two_start_df["own_pct"],
                "RoS $": two_start_df["ros_value"],
                "FTN": two_start_df["ftn_type"],
                "FTN low": two_start_df["low_bid"],
                "FTN high": two_start_df["high_bid"],
            }
        )
        st.dataframe(two_start_view, use_container_width=True, hide_index=True)
        with st.expander("Two-start schedule notes"):
            st.markdown(
                "- Sorted by schedule trust (Mon + full week first), then "
                "weekly `$`, within **My roster** then **Free agent**.\n"
                "- **Mon · full week**: team plays 7; best path to a Sunday "
                "second start.\n"
                "- **Mon · short week**: off day in the week — second start "
                "is less certain.\n"
                "- **Tue first**: usually needs all seven days to come back "
                "Sunday.\n"
                "- **Wed–Sun first**: labeled as later first starts.\n"
                "- Free-agent FTN bid columns come from "
                "`mart_faab_worksheet` when matched.\n"
                "- Other managers' rostered two-starts are hidden here."
            )

    with st.expander("How this works (v2)"):
        st.markdown(
            "- **Exact assignment**: hitters are matched to C/1B/2B/3B/SS/MI/"
            "CI/OF/UTIL with a Hungarian solver, so a player eligible at two "
            "scarce slots can no longer strand a better lineup the way the v1 "
            "greedy fill could.\n"
            "- **Pitchers**: the nine P slots are filled from weekly pitcher "
            "projections in the same pass.\n"
            "- **Monday lock vs Friday swap**: NFBC locks pitchers for the "
            "whole week on Monday and permits hitter-only swaps on Friday. "
            "Monday scores hitters on Mon–Thu dollars (pitchers still use "
            "weekly $). Friday re-optimizes hitters on Fri–Sun projections and "
            "carries the Monday pitcher set through unchanged.\n"
            "- **Expected totals**: hitting and pitching projections from the "
            "active starters only, shown as separate tables. Monday uses "
            "Mon–Thu hitter components when available; Friday uses Fri–Sun "
            "hitter components; pitchers always use full-week projections. "
            "Ratios use summed numerators/denominators "
            "(H/AB, ER/IP, (H+BB)/IP).\n"
            "- **Utility Advantage**: when two hitters are equally valuable, "
            "the less flexible one takes the exact slot so the multi-position "
            "bat stays available for UTIL/MI/CI.\n"
            "- **Scoring objective (#218)**: Neutral maximizes Razzball `$`. "
            "Team-fit multiplies each raw projection unit by "
            "`overall_points_per_raw_unit` from the weekly category plan "
            "(mobility) and linearizes AVG/ERA/WHIP around season-to-date "
            "volume. Monday Team-fit uses Mon–Thu ``mt_*`` hitter components; "
            "Friday Team-fit uses Fri–Sun ``fs_*``. Stand-alone leagues stay "
            "on Neutral.\n"
            "- **Related**: FAAB what-if is on the FAAB Worksheet tab (#187); "
            "Overall Standings packages rank/mobility and Weekly Plan "
            "maintain/stretch targets (#189 / #186). Two-start schedule "
            "buckets (#59) use first-start day + team games this week. "
            "Rate flags (#206) are observability only and never change "
            "projections or the optimizer (hitter rate flags only in the UI; "
            "pitcher start occurred/missed stays in the mart)."
        )


# ---------------------------------------------------------------------------
# Overall Standings tab (#189 §§1–3) — overall contests only
# ---------------------------------------------------------------------------

with tab_overall:
    st.subheader(f"Overall Standings — {selected_league}")
    st.caption(
        "Current contest rank and category mobility for automated overall "
        "feeds (OC, NFBC 50), plus Weekly Plan maintain/stretch vs the "
        "expected Monday team-fit lineup. Projected Finish scenarios ship "
        "separately after the projected-finish mart (#188)."
    )

    try:
        league_cfg = load_league_config()
    except Exception as e:
        st.error(f"Failed to load league_config: {e}")
        st.stop()

    overall_id = _overall_game_type_id(league_cfg, league_key)
    if overall_id is None:
        st.info(
            f"`{selected_league}` is a stand-alone league (no overall "
            "standings feed). Overall Standings and Weekly Plan require an "
            "overall contest — use the Lineup Optimizer for sit/start here. "
            "Overall leagues: OC and NFBC 50."
        )
        st.stop()

    try:
        overview_df = load_overall_overview(league_key)
        mobility_df = load_category_mobility(league_key)
        plan_df = load_weekly_category_plan(league_key)
        lineup_df = load_lineup_inputs(league_key)
        slots_df = load_roster_slots()
    except Exception as e:
        st.error(
            f"Failed to load overall standings data: {e}\n\n"
            "Confirm overall overview / mobility / weekly plan marts are "
            "built for this contest."
        )
        st.stop()

    if overview_df.empty and mobility_df.empty and plan_df.empty:
        st.warning(
            f"No overall overview, mobility, or weekly plan rows for "
            f"`{league_key}`. Check the latest NFBC snapshot ingest."
        )
        st.stop()

    # Team identity: prefer roster owner labels that match standings team
    # names (e.g. Nolen OC), not current overall rank (#189 AC).
    team_options = []
    if not plan_df.empty and "team_name" in plan_df.columns:
        team_options = sorted(plan_df["team_name"].dropna().unique().tolist())
    elif not mobility_df.empty and "team" in mobility_df.columns:
        team_options = sorted(mobility_df["team"].dropna().unique().tolist())
    elif not overview_df.empty and "team" in overview_df.columns:
        team_options = sorted(overview_df["team"].dropna().unique().tolist())

    if not team_options:
        st.warning("No standings teams found for this contest.")
        st.stop()

    lineup_owners = set()
    if not lineup_df.empty and "owner" in lineup_df.columns:
        lineup_owners = set(
            lineup_df["owner"].dropna().loc[lineup_df["owner"] != ""].unique()
        )

    team_owner_aliases = {}
    if (
        not overview_df.empty
        and "team" in overview_df.columns
        and "owner" in overview_df.columns
    ):
        for rec in (
            overview_df[["team", "owner"]]
            .drop_duplicates()
            .to_dict(orient="records")
        ):
            team_owner_aliases[rec["team"]] = rec.get("owner")

    nolen_idx = matching_index(team_options, aliases=team_owner_aliases)
    if nolen_idx is not None:
        default_idx = nolen_idx
    else:
        preferred = [t for t in team_options if t in lineup_owners]
        default_idx = team_options.index(preferred[0]) if preferred else 0

    selected_team = st.selectbox(
        "Overall team",
        team_options,
        index=default_idx,
        key=f"overall_team_{league_key}",
        help=(
            "Defaults to a %nolen% team/owner match for this league; "
            "change freely. Identity is team name / owner, not current rank."
        ),
    )

    # ------------------------------------------------------------------
    # §1 Current overall rank / points / recent change
    # ------------------------------------------------------------------
    st.markdown("### Current standings")
    my_overview = overview_df[overview_df["team"] == selected_team]
    if my_overview.empty and "owner" in overview_df.columns:
        # Rare fallback: some feeds may only match on owner label.
        my_overview = overview_df[overview_df["owner"] == selected_team]

    if my_overview.empty:
        st.warning(
            f"No overview row for team `{selected_team}` in the latest "
            f"`stg_nfbc_in_season_overall_overview` snapshot. Mobility / "
            "Weekly Plan below may still load."
        )
    else:
        row = my_overview.iloc[0]
        snap = row.get("snapshot_date")
        owner_label = row.get("owner") or "—"
        st.caption(
            f"**{selected_team}** · owner `{owner_label}` · overview "
            f"snapshot `{snap}` · contest `{league_key}`"
        )
        c1, c2, c3, c4, c5 = st.columns(5)
        rank_val = row.get("standing_rank")
        c1.metric(
            "Overall Rank",
            (
                f"{int(rank_val)}"
                if rank_val is not None and rank_val == rank_val
                else "—"
            ),
            delta=(
                None
                if row.get("rank_change") is None
                or (
                    isinstance(row.get("rank_change"), float)
                    and row.get("rank_change") != row.get("rank_change")
                )
                else f"{int(row.get('rank_change')):+d} rank"
            ),
        )
        c2.metric(
            "Hitting Pts",
            _fmt_signed(row.get("hitting_points"), 1).lstrip("+"),
        )
        c3.metric(
            "Pitching Pts",
            _fmt_signed(row.get("pitching_points"), 1).lstrip("+"),
        )
        c4.metric(
            "Total Pts",
            _fmt_signed(row.get("overall_points"), 1).lstrip("+"),
            delta=(
                None
                if row.get("points_change") is None
                or (
                    isinstance(row.get("points_change"), float)
                    and row.get("points_change") != row.get("points_change")
                )
                else f"{float(row.get('points_change')):+.1f} pts"
            ),
        )
        rc = row.get("rank_change")
        pc = row.get("points_change")
        c5.metric(
            "Recent change",
            (
                f"{_fmt_signed(pc, 1)} pts"
                if pc is not None
                and not (isinstance(pc, float) and pc != pc)
                else "—"
            ),
            delta=(
                None
                if rc is None
                or (isinstance(rc, float) and rc != rc)
                else f"{int(rc):+d} rank"
            ),
            help=(
                "NFBC overview `points_change` / `rank_change` vs the prior "
                "snapshot in the feed (not vs a hand-picked baseline)."
            ),
        )

    # ------------------------------------------------------------------
    # §2 Category mobility grid
    # ------------------------------------------------------------------
    st.markdown("### Category mobility")
    st.caption(
        "Cutline mobility from `mart_overall_category_mobility` — **not** the "
        "official category standings table. Gaps are raw-stat distance to the "
        "next distinct points island and to ±10 category points on the ladder. "
        "**Pts / raw unit** is the Team-fit weight used elsewhere."
    )

    my_mob = mobility_df[mobility_df["team"] == selected_team]
    if my_mob.empty:
        st.warning(
            f"No latest mobility rows for `{selected_team}`. Rebuild "
            "`mart_overall_category_mobility` after the overview ingest."
        )
    else:
        mob_snap = my_mob.iloc[0].get("snapshot_date")
        st.caption(f"Mobility snapshot `{mob_snap}`")

        def _mob_fmt(val, is_ratio):
            if val is None or (isinstance(val, float) and val != val):
                return "—"
            try:
                num = float(val)
            except (TypeError, ValueError):
                return "—"
            return f"{num:.3f}" if is_ratio else f"{num:.1f}"

        order_index = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        mob_sorted = my_mob.copy()
        mob_sorted["_ord"] = mob_sorted["category"].map(
            lambda c: order_index.get(c, 99)
        )
        mob_sorted = mob_sorted.sort_values(["_ord", "category"])

        mob_view = pd.DataFrame(
            {
                "Category": mob_sorted["category"],
                "Current": [
                    _mob_fmt(v, bool(r))
                    for v, r in zip(
                        mob_sorted["raw_stat"], mob_sorted["is_ratio"]
                    )
                ],
                "Cat pts": mob_sorted["category_points"].map(
                    lambda v: _mob_fmt(v, False)
                ),
                "Cat rank": mob_sorted["category_rank"].map(
                    lambda v: (
                        f"{int(v)}"
                        if v is not None and v == v
                        else "—"
                    )
                ),
                "Raw → next pts ↑": [
                    _mob_fmt(v, bool(r))
                    for v, r in zip(
                        mob_sorted["raw_gap_above"], mob_sorted["is_ratio"]
                    )
                ],
                "Raw → next pts ↓": [
                    _mob_fmt(v, bool(r))
                    for v, r in zip(
                        mob_sorted["raw_gap_below"], mob_sorted["is_ratio"]
                    )
                ],
                "Raw for +10 pts": [
                    _mob_fmt(v, bool(r))
                    for v, r in zip(
                        mob_sorted["raw_gap_up_10"], mob_sorted["is_ratio"]
                    )
                ],
                "Raw for −10 pts": [
                    _mob_fmt(v, bool(r))
                    for v, r in zip(
                        mob_sorted["raw_gap_down_10"], mob_sorted["is_ratio"]
                    )
                ],
                "Pts / raw unit": mob_sorted[
                    "overall_points_per_raw_unit"
                ].map(
                    lambda v: (
                        f"{float(v):.2f}"
                        if v is not None
                        and not (isinstance(v, float) and v != v)
                        else "—"
                    )
                ),
                "Headroom": mob_sorted["headroom_status"],
                "Tied teams": mob_sorted["teams_at_current_points"].map(
                    lambda v: (
                        f"{int(v)}"
                        if v is not None and v == v
                        else "—"
                    )
                ),
            }
        )
        st.dataframe(mob_view, use_container_width=True, hide_index=True)
        with st.expander("How to read mobility"):
            st.markdown(
                "- **Current / Cat pts / Cat rank**: season-to-date category "
                "stat and contest points for *this* team.\n"
                "- **Raw → next pts ↑/↓**: raw needed to leave the current "
                "points island for the adjacent distinct island.\n"
                "- **Raw for ±10 pts**: ladder distance on the primary "
                "decision rung (not ±1/±5).\n"
                "- **Pts / raw unit**: reciprocal slope used by Team-fit "
                "lineup weights and FAAB what-if.\n"
                "- **Headroom**: `open` / `partial` / `maxed` when near the "
                "field edge (#205)."
            )

    # ------------------------------------------------------------------
    # §3 Weekly Plan (from #186) — maintain / stretch vs expected lineup
    # ------------------------------------------------------------------
    st.markdown("### Weekly Plan")
    st.caption(
        "Maintain vs stretch targets compared to the expected Monday "
        "team-fit lineup (overall pts / mobility). Recommendation is "
        "suppressed when the gap is inside the local tie-cluster noise floor."
    )

    if plan_df.empty:
        st.warning(
            f"No rows in `mart_weekly_category_plan` for `{league_key}`. "
            "Rebuild after mobility is fresh."
        )
        st.stop()

    stretch_points = st.radio(
        "Stretch target (overall category points)",
        options=list(STRETCH_OPTIONS),
        index=list(STRETCH_OPTIONS).index(DEFAULT_STRETCH),
        horizontal=True,
        key="plan_stretch",
        help="Ladder from #183/#186. 1- and 5-point targets are intentionally omitted.",
    )

    team_plan = plan_df[plan_df["team_name"] == selected_team]
    if team_plan.empty:
        st.warning("No plan rows for that team.")
        st.stop()

    meta = team_plan.iloc[0]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Week Of", str(meta.get("week_of") or "—"))
    rank_val = meta.get("overall_rank")
    m2.metric(
        "Overall Rank",
        f"{int(rank_val)}" if rank_val is not None and rank_val == rank_val else "—",
    )
    m3.metric("Weeks Elapsed", f"{int(meta['weeks_elapsed'])}")
    m4.metric("Weeks Remaining", f"{int(meta['weeks_remaining'])}")
    st.caption(
        f"Plan snapshot {meta.get('snapshot_date')} · season "
        f"{int(meta['season_scoring_periods'])} scoring periods from "
        f"{meta.get('season_start_date')} (see `season_scoring_calendar` seed)."
    )

    roster_owner = selected_team if selected_team in lineup_owners else None

    result = None
    if roster_owner is None:
        st.warning(
            f"No `mart_weekly_lineup_inputs` owner named `{selected_team}`. "
            "Targets still show; projection/gap need a matching roster."
        )
        totals = {}
        missing_ids = []
        unfilled = []
    else:
        fmt = lineup_df["format"].dropna().iloc[0]
        hitter_slots = dict(
            zip(
                slots_df.loc[
                    (slots_df["format"] == fmt) & (slots_df["slot_group"] == "hitter"),
                    "slot",
                ].astype(str),
                slots_df.loc[
                    (slots_df["format"] == fmt) & (slots_df["slot_group"] == "hitter"),
                    "count",
                ].astype(int),
            )
        )
        pitcher_slots = dict(
            zip(
                slots_df.loc[
                    (slots_df["format"] == fmt) & (slots_df["slot_group"] == "pitcher"),
                    "slot",
                ].astype(str),
                slots_df.loc[
                    (slots_df["format"] == fmt) & (slots_df["slot_group"] == "pitcher"),
                    "count",
                ].astype(int),
            )
        )
        team_all = lineup_df[lineup_df["owner"] == roster_owner].copy()
        if "row_type" not in team_all.columns:
            team_all["row_type"] = "hitter"
        team_all["row_type"] = team_all["row_type"].fillna("hitter")

        def _parse_pos(raw):
            if raw is None:
                return []
            return [p.strip().upper() for p in str(raw).split(",") if p.strip()]

        team_all["pos_array"] = team_all["pos_raw"].apply(_parse_pos)
        players = team_all.to_dict(orient="records")
        plan_records = team_plan.to_dict(orient="records")
        # Use the helpers that existed before #281 so Streamlit Cloud can
        # rerun new app.py against a still-cached lineup_weights module.
        weights = weights_from_plan_rows(plan_records)
        ratio_context = ratio_context_from_plan_rows(plan_records)
        team_fit_ready, team_fit_msg = team_fit_inputs_ready(
            weights, ratio_context
        )
        opt_kwargs = {}
        if not team_fit_ready:
            st.warning(
                f"Team-fit unavailable for `{selected_team}`: {team_fit_msg} "
                "Falling back to Neutral `$` for the expected lineup."
            )
        else:
            opt_kwargs["weights"] = weights
            opt_kwargs["ratio_context"] = ratio_context
            st.caption(
                f"Expected lineup uses Team-fit overall-pts weights for "
                f"`{selected_team}` (not Neutral Razzball `$`)."
            )
        result = optimize_week(
            players,
            {**hitter_slots, **pitcher_slots},
            mode="monday",
            **opt_kwargs,
        )
        totals = result.totals or {}
        missing_ids = result.missing_projection_ids or []
        unfilled = result.unfilled_slots or []

    if missing_ids:
        st.info(
            f"{len(missing_ids)} rostered player(s) missing a weekly projection "
            "were scored as zero in the expected lineup."
        )
    if unfilled:
        st.warning("Unfilled slots: " + ", ".join(unfilled))

    plan_rows = build_category_plan_rows(
        team_plan.to_dict(orient="records"),
        totals,
        stretch_points=int(stretch_points),
    )
    display = pd.DataFrame(plan_rows)
    if display.empty:
        st.warning("No category rows to display.")
        st.stop()

    def _fmt_num(val, spec):
        return format(val, spec) if isinstance(val, (int, float)) else "—"

    view = pd.DataFrame(
        {
            "Category": display["category"],
            "Current": [
                _fmt_num(v, ".3f" if r else ".1f")
                for v, r in zip(display["current_raw"], display["is_ratio"])
            ],
            "Pts": display["current_category_points"].map(
                lambda v: _fmt_num(v, ".1f")
            ),
            "Maintain/wk": [
                _fmt_num(v, ".3f" if r else ".2f")
                for v, r in zip(display["maintain_weekly_target"], display["is_ratio"])
            ],
            f"Stretch +{stretch_points}/wk": [
                _fmt_num(v, ".3f" if r else ".2f")
                for v, r in zip(display["stretch_weekly_target"], display["is_ratio"])
            ],
            "Projected": [
                _fmt_num(v, ".3f" if r else ".2f")
                for v, r in zip(display["projection"], display["is_ratio"])
            ],
            "vs Maintain": display["maintain_label"],
            "vs Stretch": display["stretch_label"],
            "Recommendation": display["recommendation"],
            "Noise floor": display["noise_floor_raw"].map(
                lambda v: _fmt_num(v, ".3f")
            ),
            "Tied teams": display["teams_at_current_points"],
        }
    )

    st.markdown("#### Targets vs expected lineup")
    st.caption(
        "**Projected** = starters from the Monday optimizer using Team-fit "
        "overall-pts / mobility weights (same objective as Lineup Optimizer "
        "Team-fit). Falls back to Neutral `$` when those weights are "
        "unavailable. **Recommendation** repeats the stretch comparison and "
        "shows `no meaningful difference` when |gap| ≤ noise floor "
        "(max of tie-cluster raw width and one raw unit)."
    )
    st.dataframe(view, use_container_width=True, hide_index=True)

    with st.expander("How targets are built"):
        st.markdown(
            "- **Maintain**: counting stats use season pace "
            "(`current_raw / weeks_elapsed`); ratios use the current rate.\n"
            "- **Stretch +N**: counting adds `raw_gap_up_N / weeks_remaining` "
            "to pace; ratios use the cutline rate for +N category points.\n"
            "- **Noise floor**: `max(tie_cluster_raw_width, raw_unit_size)` so "
            "fractional projections inside a point island are not ranked.\n"
            "- **Expected lineup**: Monday `optimize_week` with Team-fit "
            "weights from this team's `overall_points_per_raw_unit` (not "
            "Neutral Razzball `$`).\n"
            "- Stand-alone leagues never enter this mart — they keep FAAB + "
            "Lineup Optimizer only."
        )


# ---------------------------------------------------------------------------
# ROS Rankings tab (#67) — rest-of-season overall rankings by league format
# ---------------------------------------------------------------------------

with tab_ros:
    st.subheader(f"ROS Rankings — {selected_league}")
    st.caption(
        "Rest-of-season dollar values from "
        "`mart_rest_of_season_overall_rankings_{oc,me,50s}` for this league's "
        "format. Filters match the draft tool (position, team, opening-day "
        "status, name). Click a column header to sort. Draft tracking is "
        "not included here."
    )

    try:
        league_cfg_ros = load_league_config()
    except Exception as e:
        st.error(f"Failed to load league_config: {e}")
        league_cfg_ros = pd.DataFrame()

    ros_fmt = format_for_league(league_cfg_ros, league_key)
    if ros_fmt is None:
        st.info(
            f"No ROS rankings mart for league `{league_key}` — "
            "`league_config.format` must be `oc`, `me`, or `50s`."
        )
    else:
        st.caption(
            f"Format **{ros_fmt}** → `{ros_table_name(ros_fmt)}`."
        )
        try:
            ros_df = load_ros_rankings(ros_fmt)
        except Exception as e:
            st.error(
                f"Failed to load ROS rankings: {e}\n\n"
                f"Confirm `{ros_table_name(ros_fmt)}` is built in "
                f"`{ATHENA_SCHEMA}`."
            )
            ros_df = pd.DataFrame()

        if ros_df.empty:
            st.warning("No ROS ranking rows returned.")
        else:
            f1, f2, f3, f4 = st.columns(4)
            pos_opts = sorted(
                {
                    p.strip()
                    for raw in ros_df["pos"].dropna()
                    for p in str(raw).replace("/", ",").split(",")
                    if p.strip()
                }
            ) if "pos" in ros_df.columns else []
            team_opts = (
                sorted(ros_df["team"].dropna().unique().tolist())
                if "team" in ros_df.columns
                else []
            )
            status_opts = (
                sorted(
                    ros_df["projected_opening_day_status"]
                    .dropna()
                    .unique()
                    .tolist()
                )
                if "projected_opening_day_status" in ros_df.columns
                else []
            )
            with f1:
                ros_positions = st.multiselect(
                    "Position (can select multiple)",
                    pos_opts,
                    help="Players who have ANY of these positions.",
                    key="ros_filter_pos",
                )
            with f2:
                ros_teams = st.multiselect(
                    "Team (can select multiple)",
                    team_opts,
                    help="Players from ANY of these teams.",
                    key="ros_filter_team",
                )
            with f3:
                ros_statuses = st.multiselect(
                    "Opening Day Status (can select multiple)",
                    status_opts,
                    help="Projected opening-day status from the FanGraphs roster file.",
                    key="ros_filter_status",
                )
            with f4:
                ros_search = st.text_input(
                    "Search Player Name",
                    key="ros_filter_search",
                )

            filtered_ros = apply_ros_filters(
                ros_df,
                positions=ros_positions,
                teams=ros_teams,
                statuses=ros_statuses,
                search_name=ros_search,
            )
            if len(filtered_ros) < len(ros_df):
                st.caption(
                    f"Showing {len(filtered_ros)} of {len(ros_df)} players."
                )
            else:
                st.caption(f"Showing all {len(ros_df)} players.")

            row_limit = st.number_input(
                "Max Rows to Display",
                min_value=100,
                max_value=5000,
                value=500,
                step=100,
                help="Limit displayed rows to reduce memory usage.",
                key="ros_row_limit",
            )
            display_ros = format_ros_display(filtered_ros)
            original_n = len(display_ros)
            if original_n > row_limit:
                display_ros = display_ros.head(int(row_limit))
                st.info(
                    f"Showing first {int(row_limit)} of {original_n} "
                    "filtered players. Raise Max Rows to Display to see more."
                )
            st.dataframe(
                display_ros,
                use_container_width=True,
                hide_index=True,
            )
