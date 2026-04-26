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

from lineup_optimizer import optimize_lineup

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

st.title("⚾ In-Season Tool")


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
ATHENA_REGION = get_config("ATHENA_REGION", "us-east-1")
ATHENA_S3_OUTPUT = get_config("ATHENA_S3_OUTPUT")

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
    return connect(
        s3_staging_dir=ATHENA_S3_OUTPUT,
        region_name=ATHENA_REGION,
        schema_name=ATHENA_SCHEMA,
        cursor_class=PandasCursor,
    )


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


st.sidebar.header("League")
selected_league = st.sidebar.selectbox("Select League", list(LEAGUES.keys()))
league_key = LEAGUES[selected_league]


tab_faab, tab_lineup = st.tabs(["FAAB Worksheet", "Lineup Optimizer"])


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
    else:
        display["pct_of_budget_display"] = ""

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
        "pct_of_budget_display": "% of Budget",
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

    out = out.rename(columns=visible)

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

    st.dataframe(out, use_container_width=True, hide_index=True, height=700)

    try:
        unmatched = load_unmatched()
        if len(unmatched) > 0:
            with st.expander(f"⚠️ {len(unmatched)} unmatched FTN players"):
                st.markdown(
                    "These FTN players could not be matched to an NFBC ID. "
                    "Add overrides to `dbt/seeds/ftn_nfbc_player_overrides.csv` "
                    "then run `dbt seed && dbt build`."
                )
                st.dataframe(unmatched, use_container_width=True, hide_index=True)
    except Exception:
        pass


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
        key="lineup_owner",
    )

    fmt = lineup_df["format"].dropna().iloc[0]
    week_of = (
        lineup_df["week_of"].dropna().iloc[0]
        if "week_of" in lineup_df.columns and not lineup_df["week_of"].dropna().empty
        else "N/A"
    )

    slot_counts_df = slots_df[
        (slots_df["format"] == fmt) & (slots_df["slot_group"] == "hitter")
    ]
    slot_counts = dict(
        zip(
            slot_counts_df["slot"].astype(str).tolist(),
            slot_counts_df["count"].astype(int).tolist(),
        )
    )

    if not slot_counts:
        st.error(
            f"No hitter slot config found for format `{fmt}` in "
            "`league_roster_slots`. Re-run `dbt seed`."
        )
        st.stop()

    team = lineup_df[lineup_df["owner"] == selected_owner].copy()

    if team.empty:
        st.warning(f"No players rostered to `{selected_owner}`.")
        st.stop()

    # Derive pos_array from pos_raw (plain comma-separated string) rather
    # than trusting the Athena array column — pyathena serializes arrays as
    # strings like "[C, 1B]" which breaks naive comma splits.
    def _parse_pos(raw):
        if raw is None:
            return []
        return [p.strip().upper() for p in str(raw).split(",") if p.strip()]

    team["pos_array"] = team["pos_raw"].apply(_parse_pos)

    players = team.to_dict(orient="records")

    result = optimize_lineup(players, slot_counts)

    active_capacity = sum(slot_counts.values())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Week Of", str(week_of))
    c2.metric("Team Hitters", len(team))
    c3.metric("Active Slots", active_capacity)
    c4.metric("Projected $", f"{result.total_score:.1f}")

    if result.unfilled_slots:
        st.warning(
            "Unfilled slots (not enough eligible hitters): "
            + ", ".join(result.unfilled_slots)
        )

    starter_cols = [
        "slot",
        "player_name",
        "team",
        "pos_raw",
        "bats",
        "num_g",
        "dollars",
        "dollars_per_game",
        "home_games",
        "away_games",
        "vs_rhp",
        "vs_lhp",
        "ros_value",
    ]

    starters_records = []
    slot_order_index = {s: i for i, s in enumerate(SLOT_DISPLAY_ORDER)}
    for a in result.starters:
        row = {"slot": a.slot}
        for k in starter_cols[1:]:
            row[k] = a.player.get(k)
        starters_records.append(row)

    starters_df = pd.DataFrame(starters_records, columns=starter_cols)
    starters_df["_slot_order"] = starters_df["slot"].map(slot_order_index).fillna(99)
    starters_df = (
        starters_df.sort_values(["_slot_order", "dollars"], ascending=[True, False])
        .drop(columns=["_slot_order"])
        .reset_index(drop=True)
    )

    for col in (
        "dollars",
        "dollars_per_game",
        "ros_value",
    ):
        if col in starters_df.columns:
            starters_df[col] = pd.to_numeric(
                starters_df[col], errors="coerce"
            ).round(1)

    st.markdown("### Starters")
    st.dataframe(
        starters_df.rename(
            columns={
                "slot": "Slot",
                "player_name": "Player",
                "team": "Team",
                "pos_raw": "Pos",
                "bats": "B",
                "num_g": "G",
                "dollars": "Wk $",
                "dollars_per_game": "Wk $/G",
                "home_games": "H",
                "away_games": "A",
                "vs_rhp": "vR",
                "vs_lhp": "vL",
                "ros_value": "RoS $",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    bench_cols = [
        "player_name",
        "team",
        "pos_raw",
        "bats",
        "num_g",
        "dollars",
        "dollars_per_game",
        "home_games",
        "away_games",
        "vs_rhp",
        "vs_lhp",
        "ros_value",
    ]
    bench_records = [
        {k: p.get(k) for k in bench_cols} for p in result.bench
    ]
    bench_df = pd.DataFrame(bench_records, columns=bench_cols)
    for col in ("dollars", "dollars_per_game", "ros_value"):
        if col in bench_df.columns:
            bench_df[col] = pd.to_numeric(bench_df[col], errors="coerce").round(1)

    st.markdown(f"### Bench ({len(bench_df)})")
    st.dataframe(
        bench_df.rename(
            columns={
                "player_name": "Player",
                "team": "Team",
                "pos_raw": "Pos",
                "bats": "B",
                "num_g": "G",
                "dollars": "Wk $",
                "dollars_per_game": "Wk $/G",
                "home_games": "H",
                "away_games": "A",
                "vs_rhp": "vR",
                "vs_lhp": "vL",
                "ros_value": "RoS $",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("How this works (v1)"):
        st.markdown(
            "- **Greedy Monday-lock**: for each slot in a fixed order (C, SS, "
            "2B, 3B, 1B, OF, MI, CI, UTIL), fill with the highest full-week "
            "`dollars` unassigned eligible hitter.\n"
            "- **Score**: Razzball weekly $ (full Mon–Sun). v2 adds a "
            "Friday-lock view with the weekend-only file.\n"
            "- **Known limitation**: greedy can be suboptimal when a player "
            "is eligible at multiple scarce slots. MILP global optimum lands "
            "in v3.\n"
            "- **Scope**: hitters only. Pitcher streaming / two-start planner "
            "is Phase 1c."
        )
