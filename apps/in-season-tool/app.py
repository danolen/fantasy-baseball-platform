"""
Fantasy Baseball In-Season Tool

FAAB worksheet combining FTN recommendations, rest-of-season projections,
and weekly projections for NFBC league management.
"""

import streamlit as st
import pandas as pd
import numpy as np
import os
from pyathena import connect
from pyathena.pandas.cursor import PandasCursor
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="In-Season Tool",
    page_icon="⚾",
    layout="wide",
)

st.title("⚾ In-Season Tool")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# League display labels → mart league keys
# ---------------------------------------------------------------------------

LEAGUES = {
    "OC": "nolen_oc",
    "Cash 12": "nolen_cash_12",
    "OCQ": "nolen_ocq",
    "Cash 15": "nolen_cash_15",
}

# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def _optimize_df(df):
    """Reduce memory by downcasting numerics and converting low-cardinality strings to category."""
    for col in df.select_dtypes(include=["object"]).columns:
        if df[col].nunique() / max(len(df), 1) < 0.5:
            df[col] = df[col].astype("category")
    for col in df.select_dtypes(include=["int64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="integer")
    for col in df.select_dtypes(include=["float64"]).columns:
        df[col] = pd.to_numeric(df[col], downcast="float")
    return df

# ---------------------------------------------------------------------------
# Data loaders (cached per league)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Sidebar — league & filters
# ---------------------------------------------------------------------------

st.sidebar.header("League")
selected_league = st.sidebar.selectbox("Select League", list(LEAGUES.keys()))

st.sidebar.header("Filters")
ftn_only = st.sidebar.checkbox("FTN recommended only", value=False)

# ---------------------------------------------------------------------------
# Load data (single query)
# ---------------------------------------------------------------------------

try:
    df = load_faab_data(LEAGUES[selected_league])
except Exception as e:
    st.error(f"Failed to load data from Athena: {e}")
    st.stop()

# ---------------------------------------------------------------------------
# Remaining sidebar filters (depend on loaded data)
# ---------------------------------------------------------------------------

all_positions = sorted(
    {
        p.strip()
        for pos in df["position"].dropna().unique()
        for p in str(pos).split(",")
    }
)
selected_positions = st.sidebar.multiselect("Position", all_positions, default=all_positions)

all_types = sorted(df.loc[df["has_ftn_rec"] == 1, "ftn_type"].dropna().unique().tolist())
selected_types = st.sidebar.multiselect("FTN Type", all_types, default=all_types)

hide_owned = st.sidebar.checkbox("Hide owned players", value=True)
search = st.sidebar.text_input("Search player")

# ---------------------------------------------------------------------------
# Apply filters via boolean mask (no DataFrame copy)
# ---------------------------------------------------------------------------

mask = pd.Series(True, index=df.index)

if ftn_only:
    mask &= df["has_ftn_rec"] == 1

if selected_positions:
    pos_pattern = "|".join(selected_positions)
    mask &= df["position"].str.contains(pos_pattern, na=False)

if selected_types and ftn_only:
    mask &= df["ftn_type"].isin(selected_types)

if hide_owned:
    mask &= df["owner"].isna() | (df["owner"] == "")

if search:
    mask &= df["player"].str.contains(search, case=False, na=False)

display = df.loc[mask]

# ---------------------------------------------------------------------------
# Build display table
# ---------------------------------------------------------------------------

player_label = display["player"].fillna("")
status = display["status_tag"].fillna("")
bid_chg = display["bid_change"].fillna("")
player_label = np.where(status != "", player_label + " (" + status + ")", player_label)
player_label = np.where(
    bid_chg != "",
    player_label + np.where(bid_chg == "raised", " ↑", " ↓"),
    player_label,
)

COLUMNS = {
    "player_label": "Player",
    "position": "Pos",
    "team": "Team",
    "ftn_type": "Type",
    "low_bid": "Low $",
    "high_bid": "High $",
    "ros_value": "RoS $",
    "dollars": "Wk $",
    "dollars_per_game": "Wk $/G",
    "num_g": "G",
    "opps": "Matchups",
    "dollars_monday_thursday": "M-Th $",
    "dollars_friday_sunday": "F-Su $",
    "owner": "Owner",
    "own_pct": "Own%",
    "ftn_notes": "Notes",
}

visible = {k: v for k, v in COLUMNS.items() if k in display.columns or k == "player_label"}
out = display[[c for c in visible if c in display.columns]].copy()
out.insert(0, "player_label", player_label)

for col in ("ros_value", "dollars", "dollars_per_game", "dollars_monday_thursday", "dollars_friday_sunday"):
    if col in out.columns:
        out[col] = pd.to_numeric(out[col], errors="coerce").round(1)

out = out.sort_values(
    ["has_ftn_rec", "high_bid", "ros_value"],
    ascending=[False, False, False],
    na_position="last",
)

out = out[[c for c in visible if c in out.columns]].rename(columns=visible)

# ---------------------------------------------------------------------------
# Main display
# ---------------------------------------------------------------------------

st.subheader(f"FAAB Worksheet — {selected_league}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Players", len(out))
c2.metric("FTN Recs", int((display["has_ftn_rec"] == 1).sum()))
week_val = (
    display["week_of"].dropna().iloc[0]
    if "week_of" in display.columns and not display["week_of"].dropna().empty
    else "N/A"
)
c3.metric("Week Of", week_val)
unowned_count = len(display[display["owner"].isna() | (display["owner"] == "")]) if "owner" in display.columns else "—"
c4.metric("Unowned", unowned_count)

st.dataframe(out, use_container_width=True, hide_index=True, height=700)

# ---------------------------------------------------------------------------
# Unmatched players warning
# ---------------------------------------------------------------------------

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
