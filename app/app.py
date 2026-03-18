"""
Simple Fantasy Baseball Draft Tool

This is a basic Streamlit app to view player rankings from your dbt mart tables.
We'll build this up incrementally, keeping it simple and understandable.
"""

import streamlit as st
import pandas as pd
import os
from datetime import datetime
from pyathena import connect
from pyathena.pandas.cursor import PandasCursor
from dotenv import load_dotenv
import boto3
from botocore.exceptions import ClientError
import plotly.graph_objects as go
import numpy as np

# Load environment variables from .env file (if it exists)
# This makes it easy to set config without hardcoding values
# Note: On Streamlit Cloud, use st.secrets instead (see deployment docs)
load_dotenv()

# Page configuration
st.set_page_config(
    page_title="Fantasy Baseball Draft Tool",
    page_icon="⚾",
    layout="wide"
)

# Title
st.title("⚾ Fantasy Baseball Draft Tool")
st.markdown("View player rankings from your dbt mart tables")

# Page navigation
page = st.radio(
    "Navigation",
    ["📊 Draft Table", "📈 ADP Chart"],
    horizontal=True,
    label_visibility="collapsed"
)
st.markdown("---")

# Configuration - supports both Streamlit Secrets (for cloud) and .env files (for local)
# Priority: st.secrets > environment variables > .env file > defaults
def get_config(key, default=None):
    """Get configuration value from Streamlit secrets, env vars, or default"""
    # First try Streamlit secrets (for Streamlit Cloud)
    try:
        # Method 1: Try accessing through 'default' section (most common in Streamlit Cloud)
        # Secrets are typically structured as: [default] ATHENA_S3_OUTPUT = "value"
        if "default" in st.secrets:
            if key in st.secrets["default"]:
                return st.secrets["default"][key]
            # Try as attribute access
            if hasattr(st.secrets["default"], key):
                return getattr(st.secrets["default"], key)
        
        # Method 2: Try accessing directly at top level
        # Some users might put secrets at top level without [default] section
        if key in st.secrets:
            return st.secrets[key]
        if hasattr(st.secrets, key):
            return getattr(st.secrets, key)
            
    except (AttributeError, KeyError, TypeError):
        pass
    
    # Fall back to environment variables or .env file
    return os.getenv(key, default)

ATHENA_DATABASE = get_config("ATHENA_DATABASE", "AwsDataCatalog")
ATHENA_SCHEMA = get_config("ATHENA_SCHEMA", "dbt_main")
ATHENA_REGION = get_config("ATHENA_REGION", "us-east-1")
ATHENA_S3_OUTPUT = get_config("ATHENA_S3_OUTPUT")  # Required - no default

# DynamoDB configuration for draft tracking
DYNAMODB_REGION = get_config("DYNAMODB_REGION", ATHENA_REGION)
DYNAMODB_TABLE_NAME = get_config("DYNAMODB_TABLE_NAME", "fantasy_baseball_draft")

# Configure AWS credentials from Streamlit Secrets or environment variables
# boto3/pyathena will automatically use these environment variables
AWS_ACCESS_KEY_ID = get_config("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = get_config("AWS_SECRET_ACCESS_KEY")
AWS_DEFAULT_REGION = get_config("AWS_DEFAULT_REGION", ATHENA_REGION)

# Set AWS credentials as environment variables if they were found in secrets
# This allows boto3 to automatically pick them up
if AWS_ACCESS_KEY_ID and not os.getenv("AWS_ACCESS_KEY_ID"):
    os.environ["AWS_ACCESS_KEY_ID"] = AWS_ACCESS_KEY_ID
if AWS_SECRET_ACCESS_KEY and not os.getenv("AWS_SECRET_ACCESS_KEY"):
    os.environ["AWS_SECRET_ACCESS_KEY"] = AWS_SECRET_ACCESS_KEY
if AWS_DEFAULT_REGION and not os.getenv("AWS_DEFAULT_REGION"):
    os.environ["AWS_DEFAULT_REGION"] = AWS_DEFAULT_REGION

# Validate required configuration
if not ATHENA_S3_OUTPUT:
    st.error("""
    **Configuration Error:** `ATHENA_S3_OUTPUT` is required but not set.
    
    **For Local Development:**
    - Create a `.env` file with: `ATHENA_S3_OUTPUT=s3://your-bucket/query-results/`
    - Or set it as an environment variable
    
    **For Streamlit Cloud:**
    - Go to your app settings → Secrets
    - Add `ATHENA_S3_OUTPUT` in the `[default]` section:
      ```toml
      [default]
      ATHENA_S3_OUTPUT = "s3://your-bucket/query-results/"
      ```
    - Make sure to use quotes around the S3 path
    - See DEPLOYMENT.md for full instructions
    """)
    st.stop()

# SIMPLE DYNAMODB FUNCTIONS FOR DRAFT TRACKING
def get_dynamodb_table(table_name, region):
    """Get or create DynamoDB table for draft tracking"""
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        table.load()
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            table = dynamodb.create_table(
                TableName=table_name,
                KeySchema=[{'AttributeName': 'player_id', 'KeyType': 'HASH'}],
                AttributeDefinitions=[{'AttributeName': 'player_id', 'AttributeType': 'S'}],
                BillingMode='PAY_PER_REQUEST'
            )
            table.wait_until_exists()
            st.success(f"Created DynamoDB table: {table_name}")
    
    return table

def mark_player_drafted(table, player_id, player_name=None):
    """Mark a player as drafted in DynamoDB"""
    try:
        # Get existing item if it exists to preserve "drafted_to_my_team" status
        existing_item = {}
        try:
            response = table.get_item(Key={'player_id': str(player_id)})
            if 'Item' in response:
                existing_item = response['Item']
        except:
            pass
        
        table.put_item(
            Item={
                'player_id': str(player_id),
                'drafted': True,
                'drafted_at': datetime.now().isoformat(),
                'player_name': player_name or str(player_id),
                'drafted_to_my_team': existing_item.get('drafted_to_my_team', False)  # Preserve my team status
            }
        )
        return True
    except Exception as e:
        st.error(f"Error marking player as drafted: {str(e)}")
        return False

def mark_player_undrafted(table, player_id):
    """Mark a player as undrafted (remove from DynamoDB)"""
    try:
        table.delete_item(Key={'player_id': str(player_id)})
        return True
    except Exception as e:
        st.error(f"Error marking player as undrafted: {str(e)}")
        return False

def mark_player_to_my_team(table, player_id, player_name=None, is_my_team=True):
    """Mark a player as drafted to my team (also marks as drafted)"""
    try:
        table.put_item(
            Item={
                'player_id': str(player_id),
                'drafted': True,  # Always mark as drafted if on my team
                'drafted_to_my_team': is_my_team,
                'drafted_at': datetime.now().isoformat(),
                'player_name': player_name or str(player_id)
            }
        )
        return True
    except Exception as e:
        st.error(f"Error marking player to my team: {str(e)}")
        return False

def get_drafted_players(table, draft_session_id, force_refresh=False):
    """Get set of all drafted player IDs (cached in session state)"""
    cache_key = f"drafted_players_{draft_session_id}"
    
    # Return cached result if available and not forcing refresh
    if not force_refresh and cache_key in st.session_state:
        return st.session_state[cache_key]
    
    try:
        response = table.scan()
        drafted_ids = set()
        for item in response.get('Items', []):
            if item.get('drafted', False):
                drafted_ids.add(item['player_id'])
        
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            for item in response.get('Items', []):
                if item.get('drafted', False):
                    drafted_ids.add(item['player_id'])
        
        # Cache the result
        st.session_state[cache_key] = drafted_ids
        return drafted_ids
    except Exception as e:
        st.warning(f"Error getting drafted players: {str(e)}")
        return set()

def get_my_team_players(table, draft_session_id, force_refresh=False):
    """Get set of all player IDs drafted to my team (cached in session state)"""
    cache_key = f"my_team_players_{draft_session_id}"
    
    # Return cached result if available and not forcing refresh
    if not force_refresh and cache_key in st.session_state:
        return st.session_state[cache_key]
    
    try:
        response = table.scan()
        my_team_ids = set()
        for item in response.get('Items', []):
            if item.get('drafted_to_my_team', False):
                my_team_ids.add(item['player_id'])
        
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            for item in response.get('Items', []):
                if item.get('drafted_to_my_team', False):
                    my_team_ids.add(item['player_id'])
        
        # Cache the result
        st.session_state[cache_key] = my_team_ids
        return my_team_ids
    except Exception as e:
        st.warning(f"Error getting my team players: {str(e)}")
        return set()

@st.cache_data(ttl=3600)
def load_percentiles(format_type, schema, s3_output, region):
    """Load SGP percentiles from Athena, cached for 1 hour across all sessions."""
    conn = connect(
        s3_staging_dir=s3_output,
        region_name=region,
        schema_name=schema,
        cursor_class=PandasCursor
    )
    query = f"""
    WITH filename_parts AS (
        SELECT 
            _filename,
            category,
            p80,
            p90,
            split_part(_filename, ' ', 2) as format_part,
            cast(split_part(_filename, ' ', 3) as int) as year_part
        FROM {schema}.mart_sgp_percentiles
    )
    SELECT 
        category,
        p80,
        p90
    FROM filename_parts
    WHERE format_part = '{format_type}'
    AND year_part = (SELECT max(year_part) FROM filename_parts WHERE format_part = '{format_type}')
    """
    cursor = conn.cursor()
    return cursor.execute(query).as_pandas()


@st.cache_data(ttl=900)
def load_rankings(table_name, schema, s3_output, region):
    """Load player rankings from Athena, cached for 15 minutes across all sessions."""
    conn = connect(
        s3_staging_dir=s3_output,
        region_name=region,
        schema_name=schema,
        cursor_class=PandasCursor
    )
    columns_needed = [
        'id', 'name', 'team', 'pos', 'rank', 'adp', 'min_pick', 'max_pick',
        'rank_diff', 'projected_opening_day_status', 'value',
        'pa', 'ab', 'r', 'hr', 'rbi', 'sb', 'avg', 'obp', 'slg',
        'ip', 'k', 'w', 'sv', 'era', 'whip'
    ]
    columns_str = ', '.join(columns_needed)
    query = f"SELECT {columns_str} FROM {schema}.{table_name} ORDER BY rank"
    cursor = conn.cursor()
    df = cursor.execute(query).as_pandas()
    return optimize_dataframe_memory(df)


def optimize_dataframe_memory(df):
    """Optimize DataFrame memory usage by converting to efficient dtypes"""
    df = df.copy()  # Work on a copy to avoid modifying original
    
    # Convert string columns to category (much more memory efficient)
    # Be more aggressive - convert if less than 70% unique (was 50%)
    for col in df.columns:
        if df[col].dtype == 'object':
            unique_ratio = df[col].nunique() / len(df) if len(df) > 0 else 1
            if unique_ratio < 0.7:  # More aggressive: convert if less than 70% unique
                try:
                    df[col] = df[col].astype('category')
                except:
                    pass  # Skip if conversion fails
    
    # Downcast numeric columns to smaller types
    for col in df.select_dtypes(include=['int64']).columns:
        df[col] = pd.to_numeric(df[col], downcast='integer')
    
    for col in df.select_dtypes(include=['float64']).columns:
        df[col] = pd.to_numeric(df[col], downcast='float')
    
    # Convert specific known columns to category for better memory usage
    category_columns = ['team', 'pos', 'projected_opening_day_status']
    for col in category_columns:
        if col in df.columns and df[col].dtype == 'object':
            try:
                df[col] = df[col].astype('category')
            except:
                pass
    
    return df

def list_draft_sessions(region, table_name_prefix):
    """List all existing draft session tables"""
    try:
        dynamodb = boto3.client('dynamodb', region_name=region)
        response = dynamodb.list_tables()
        
        # Filter tables that match our draft table prefix
        all_tables = response.get('TableNames', [])
        draft_tables = [t for t in all_tables if t.startswith(table_name_prefix + '_')]
        
        # Extract session IDs from table names (remove prefix and underscore)
        session_ids = [t.replace(table_name_prefix + '_', '') for t in draft_tables]
        
        # Handle pagination
        while 'LastEvaluatedTableName' in response:
            response = dynamodb.list_tables(ExclusiveStartTableName=response['LastEvaluatedTableName'])
            all_tables = response.get('TableNames', [])
            draft_tables = [t for t in all_tables if t.startswith(table_name_prefix + '_')]
            session_ids.extend([t.replace(table_name_prefix + '_', '') for t in draft_tables])
        
        return sorted(session_ids)
    except Exception as e:
        st.warning(f"Error listing draft sessions: {str(e)}")
        return []

# Draft session ID - dropdown with existing sessions + option to create new
existing_sessions = list_draft_sessions(DYNAMODB_REGION, DYNAMODB_TABLE_NAME)
default_session = os.getenv("DRAFT_SESSION_ID", "default_draft")

# Add option to create new session
session_options = ["➕ Create New Session..."] + existing_sessions

# If default session exists, use it; otherwise use first option
if default_session in existing_sessions:
    default_index = existing_sessions.index(default_session) + 1  # +1 because of "Create New" option
else:
    default_index = 0  # Default to "Create New"

selected_session_option = st.selectbox(
    "Draft Session",
    session_options,
    index=default_index,
    help="Select an existing draft session or create a new one"
)

# Handle session selection
if selected_session_option == "➕ Create New Session...":
    # Show text input for new session name
    new_session_id = st.text_input(
        "New Draft Session ID",
        value="",
        help="Enter a unique name for your new draft session",
        key="new_session_input"
    )
    if new_session_id:
        draft_session_id = new_session_id
    else:
        # Use a default if nothing entered
        draft_session_id = default_session
else:
    # Use the selected existing session
    draft_session_id = selected_session_option

# Draft type selection (Mock vs Live)
draft_type_key = f"draft_type_{draft_session_id}"

# Use default index of 1 (Live Draft) - widget with key will manage session state automatically
# Don't access st.session_state[draft_type_key] before widget creation to avoid conflicts
draft_type = st.radio(
    "Draft Type",
    ["Mock Draft", "Live Draft"],
    index=1,  # Default to Live Draft (index 1)
    horizontal=True,
    key=draft_type_key,
    help="Mock Draft: Enable simulation features. Live Draft: Track actual draft picks only."
)
# Note: st.radio with key automatically updates session_state
# The widget will remember the user's selection on subsequent runs

# Format selection
format_type = st.selectbox("Select Format", ["50s", "OC", "ME"])

# Clear cache from other format when switching (memory optimization)
for other_format in ["50s", "OC", "ME"]:
    if other_format != format_type:
        other_cache_key = f"player_data_{other_format}"
        if other_cache_key in st.session_state:
            # Keep timestamp but clear data to free memory
            del st.session_state[other_cache_key]

# Table name based on format
table_name = f"mart_preseason_overall_rankings_{format_type.lower()}"

# CACHING EXPLANATION:
# st.session_state is Streamlit's way to store data in memory between button clicks
# We'll use it to store the player data so we don't query Athena every time
# Key: format_type (so 50s and OC have separate cached data)
cache_key = f"player_data_{format_type}"
timestamp_key = f"cache_timestamp_{format_type}"

# Check if we already have data cached for this format
if cache_key not in st.session_state:
    # No cached data - we'll need to load it
    st.session_state[cache_key] = None
    st.session_state[timestamp_key] = None

# Button to refresh/load data
# Refresh button to clear cache and reload data
refresh_button = st.button("🔄 Refresh Data", help="Clear cached data and reload from Athena")

# If refresh button clicked, clear both caches and recalculate pick counter
if refresh_button:
    load_rankings.clear()
    st.session_state[cache_key] = None
    st.session_state[timestamp_key] = None
    # Recalculate pick counter from DynamoDB to sync with other devices
    try:
        draft_table_name = f"{DYNAMODB_TABLE_NAME}_{draft_session_id}"
        draft_table = get_dynamodb_table(draft_table_name, DYNAMODB_REGION)
        total_drafted_count = len(get_drafted_players(draft_table, draft_session_id, force_refresh=True))
        pick_key = f"current_pick_{draft_session_id}_{format_type}"
        st.session_state[pick_key] = total_drafted_count + 1
    except Exception as e:
        pass  # Silently fail if DynamoDB isn't available
    st.info("Cache cleared! Data will reload automatically.")

# Load data automatically if we don't have cached data yet
if st.session_state[cache_key] is None:
    with st.spinner("Loading data from Athena..."):
        try:
            df = load_rankings(table_name, ATHENA_SCHEMA, ATHENA_S3_OUTPUT, ATHENA_REGION)
            st.session_state[cache_key] = df
            st.session_state[timestamp_key] = datetime.now()
            st.success(f"Loaded {len(df)} players from Athena!")
        except Exception as e:
            st.error(f"Error loading data: {str(e)}")
            st.info("""
            **Troubleshooting:**
            1. Make sure AWS credentials are configured (run `aws configure`)
            2. Check your .env file or environment variables
            3. Make sure your dbt models are built (`dbt build --select mart_*`)
            4. Check that the schema and table names match your setup
            """)

# Helper function to render filters and return filtered dataframe
def render_filters_and_apply(df, draft_table, draft_session_id):
    """Render filter UI and return filtered dataframe"""
    st.subheader("Filters")
    
    # Initialize filter state in session_state if not exists
    filter_key = f"filters_{format_type}"
    if filter_key not in st.session_state:
        st.session_state[filter_key] = {
            'selected_positions': [],
            'selected_teams': [],
            'selected_statuses': [],
            'search_name': '',
            'draft_filter': 'All'
        }
    
    # FILTERING SECTION
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        # Filter by position (multi-select)
        if 'pos' in df.columns:
            all_positions = set()
            for pos_str in df['pos'].dropna():
                if isinstance(pos_str, str):
                    positions = pos_str.replace('/', ',').split(',')
                    for p in positions:
                        all_positions.add(p.strip())
            positions_list = sorted(list(all_positions))
            # Widget with key automatically manages its own session state
            # Read from widget's session state, with fallback to our stored value
            widget_key = f"filter_pos_{format_type}"
            if widget_key not in st.session_state:
                st.session_state[widget_key] = st.session_state[filter_key]['selected_positions']
            
            selected_positions = st.multiselect(
                "Position (can select multiple)", 
                positions_list,
                default=st.session_state[widget_key],
                help="Select one or more positions. Shows players who have ANY of these positions.",
                key=widget_key
            )
            # Update our filter state from widget's session state
            st.session_state[filter_key]['selected_positions'] = st.session_state[widget_key]
        else:
            selected_positions = []
    
    with col2:
        # Filter by team (multi-select)
        if 'team' in df.columns:
            teams = sorted(df['team'].dropna().unique().tolist())
            # Widget with key automatically manages its own session state
            widget_key = f"filter_team_{format_type}"
            if widget_key not in st.session_state:
                st.session_state[widget_key] = st.session_state[filter_key]['selected_teams']
            
            selected_teams = st.multiselect(
                "Team (can select multiple)", 
                teams,
                default=st.session_state[widget_key],
                help="Select one or more teams. Shows players from ANY of these teams.",
                key=widget_key
            )
            # Update our filter state from widget's session state
            st.session_state[filter_key]['selected_teams'] = st.session_state[widget_key]
        else:
            selected_teams = []
    
    with col3:
        # Filter by projected opening day status (multi-select)
        if 'projected_opening_day_status' in df.columns:
            statuses = sorted(df['projected_opening_day_status'].dropna().unique().tolist())
            # Widget with key automatically manages its own session state
            widget_key = f"filter_status_{format_type}"
            if widget_key not in st.session_state:
                st.session_state[widget_key] = st.session_state[filter_key]['selected_statuses']
            
            selected_statuses = st.multiselect(
                "Opening Day Status (can select multiple)",
                statuses,
                default=st.session_state[widget_key],
                help="Select one or more opening day statuses. Shows players with ANY of these statuses.",
                key=widget_key
            )
            # Update our filter state from widget's session state
            st.session_state[filter_key]['selected_statuses'] = st.session_state[widget_key]
        else:
            selected_statuses = []
    
    with col4:
        # Search by player name
        # Widget with key automatically manages its own session state
        widget_key = f"filter_search_{format_type}"
        if widget_key not in st.session_state:
            st.session_state[widget_key] = st.session_state[filter_key]['search_name']
        
        search_name = st.text_input(
            "Search Player Name", 
            value=st.session_state[widget_key],
            key=widget_key
        )
        # Update our filter state from widget's session state
        st.session_state[filter_key]['search_name'] = st.session_state[widget_key]
    
    # DRAFT STATUS - Get drafted players from DynamoDB (cached)
    drafted_player_ids = get_drafted_players(draft_table, draft_session_id)
    my_team_player_ids = get_my_team_players(draft_table, draft_session_id)
    
    # Recalculate pick counter from DynamoDB to sync with other devices
    # This ensures the counter is always accurate, even if changes were made on another device
    pick_key = f"current_pick_{draft_session_id}_{format_type}"
    total_drafted_count = len(drafted_player_ids)
    st.session_state[pick_key] = total_drafted_count + 1
    
    # Add drafted status columns to dataframe
    if 'id' in df.columns:
        df['Drafted'] = df['id'].astype(str).isin(drafted_player_ids)
        df['My Team'] = df['id'].astype(str).isin(my_team_player_ids)
    
    # Filter by draft status
    # Widget with key automatically manages its own session state
    widget_key = f"filter_draft_{format_type}"
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state[filter_key]['draft_filter']
    
    # Get index for current value
    current_filter = st.session_state[widget_key]
    filter_options = ["All", "Drafted Only", "Undrafted Only", "My Team Only"]
    try:
        current_index = filter_options.index(current_filter)
    except ValueError:
        current_index = 0
    
    draft_filter = st.radio(
        "Draft Status",
        filter_options,
        index=current_index,
        horizontal=True,
        key=widget_key
    )
    # Update our filter state from widget's session state
    st.session_state[filter_key]['draft_filter'] = st.session_state[widget_key]
    
    # Apply filters to the dataframe
    filtered_df = df.copy()
    
    # Filter by position
    if selected_positions and 'pos' in filtered_df.columns:
        mask = filtered_df['pos'].astype(str).apply(
            lambda pos: any(sel_pos in str(pos) for sel_pos in selected_positions)
        )
        filtered_df = filtered_df[mask]
    
    # Filter by team
    if selected_teams and 'team' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['team'].isin(selected_teams)]
    
    # Filter by projected opening day status
    if selected_statuses and 'projected_opening_day_status' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['projected_opening_day_status'].isin(selected_statuses)]
    
    # Search by name
    if search_name and 'name' in filtered_df.columns:
        filtered_df = filtered_df[
            filtered_df['name'].str.contains(search_name, case=False, na=False)
        ]
    
    # Filter by draft status
    if draft_filter == "Drafted Only" and 'Drafted' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['Drafted'] == True]
    elif draft_filter == "Undrafted Only" and 'Drafted' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['Drafted'] == False]
    elif draft_filter == "My Team Only" and 'My Team' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['My Team'] == True]
    
    return filtered_df, draft_table

# Display the data if we have it cached
if st.session_state[cache_key] is not None:
    df = st.session_state[cache_key].copy()  # Make a copy so we don't modify the cached data
    cached_time = st.session_state[timestamp_key]
    
    # Format the timestamp nicely
    if cached_time:
        time_str = cached_time.strftime("%Y-%m-%d %H:%M:%S")
        st.caption(f"📅 Data cached at: {time_str}")
    
    # Get draft table
    draft_table_name = f"{DYNAMODB_TABLE_NAME}_{draft_session_id}"
    draft_table = get_dynamodb_table(draft_table_name, DYNAMODB_REGION)
    
    # Render filters and get filtered dataframe
    filtered_df, draft_table = render_filters_and_apply(df, draft_table, draft_session_id)
    
    # Show summary
    st.markdown("---")
    if len(filtered_df) < len(df):
        st.caption(f"Filtered from {len(df)} total players (use Refresh button to reload)")
    else:
        st.caption(f"Showing all {len(df)} players (use Refresh button to reload)")
    
    # Show draft summary
    if 'Drafted' in df.columns:
        total_drafted = df['Drafted'].sum()
        total_my_team = df['My Team'].sum() if 'My Team' in df.columns else 0
        st.info(f"📊 **Draft Summary:** {total_drafted} players drafted ({total_my_team} on my team) out of {len(df)} total players")
    
    # Get draft type (needed for both mock draft section and manual drafting)
    draft_type_key = f"draft_type_{draft_session_id}"
    current_draft_type = st.session_state.get(draft_type_key, "Live Draft")
    
    # Render different pages based on selection
    if page == "📊 Draft Table":
        # DRAFT TABLE PAGE
        
        # MOCK DRAFT SIMULATION (only show if Mock Draft type)
        
        if current_draft_type == "Mock Draft":
            st.markdown("---")
            st.subheader("Mock Draft Simulation")
            
            # Initialize current pick in session state
            pick_key = f"current_pick_{draft_session_id}_{format_type}"
            last_picked_key = f"last_picked_{draft_session_id}_{format_type}"
            
            if pick_key not in st.session_state:
                st.session_state[pick_key] = 1
            if last_picked_key not in st.session_state:
                st.session_state[last_picked_key] = None
            
            current_pick = st.session_state[pick_key]
            last_picked = st.session_state[last_picked_key]
            
            # Show current pick and last picked player
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Current Pick", current_pick)
            with col2:
                if last_picked:
                    st.info(f"Last Picked: **{last_picked}**")
                else:
                    st.info("No picks yet")
            with col3:
                if st.button("🔄 Reset Mock Draft", help="Reset pick counter and clear all drafted players"):
                    # Clear all drafted players from DynamoDB
                    try:
                        drafted_ids = get_drafted_players(draft_table, draft_session_id, force_refresh=True)
                        for player_id in drafted_ids:
                            mark_player_undrafted(draft_table, player_id)
                        # Clear cache after reset
                        if f"drafted_players_{draft_session_id}" in st.session_state:
                            del st.session_state[f"drafted_players_{draft_session_id}"]
                        if f"my_team_players_{draft_session_id}" in st.session_state:
                            del st.session_state[f"my_team_players_{draft_session_id}"]
                        st.session_state[pick_key] = 1
                        st.session_state[last_picked_key] = None
                        st.success("Mock draft reset! All players cleared.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error resetting draft: {str(e)}")
        
        # Simulate next pick button (only show for Mock Draft)
        if current_draft_type == "Mock Draft":
            if st.button("🎲 Simulate Next Pick", type="primary", use_container_width=True):
                # Get undrafted players (exclude players on "My Team")
                undrafted_df = filtered_df[
                    (filtered_df.get('Drafted', False) == False) & 
                    (filtered_df.get('My Team', False) == False)
                ].copy()
                
                # Filter to players with ADP data
                if 'adp' in undrafted_df.columns and 'min_pick' in undrafted_df.columns and 'max_pick' in undrafted_df.columns:
                    players_with_adp = undrafted_df[
                        undrafted_df['adp'].notna() & 
                        undrafted_df['min_pick'].notna() & 
                        undrafted_df['max_pick'].notna()
                    ].copy()
                    
                    if len(players_with_adp) > 0:
                        # Calculate draft probabilities
                        # Use normal distribution centered on ADP, with variance based on range
                        # Allow ~15% probability outside min/max range (reaches and falls happen)
                        probabilities = []
                        player_ids = []
                        player_names = []
                        
                        for _, player in players_with_adp.iterrows():
                            adp = player['adp']
                            min_pick = player['min_pick']
                            max_pick = player['max_pick']
                            player_id = str(player['id'])
                            player_name = player.get('name', 'Unknown')
                            
                            # Calculate variance based on range
                            range_size = max_pick - min_pick
                            std_dev = max(range_size / 3, 3)  # Minimum std_dev of 3
                            
                            # Base probability using normal distribution centered on ADP
                            base_prob = np.exp(-0.5 * ((current_pick - adp) / std_dev) ** 2)
                            
                            # Apply range constraints and urgency factors
                            if current_pick < min_pick:
                                # Too early - very low probability, only if very close
                                distance_before_min = min_pick - current_pick
                                if distance_before_min <= 2:
                                    prob = base_prob * 0.1  # 10% if within 2 picks
                                else:
                                    prob = 0.0001  # Essentially zero
                            elif current_pick > max_pick:
                                # Past max_pick - player is overdue, boost probability significantly
                                distance_after_max = current_pick - max_pick
                                # Urgency factor: the further past max, the higher the urgency
                                urgency_factor = 1 + (distance_after_max * 2)  # Exponential urgency
                                prob = base_prob * urgency_factor * 10  # Strong boost for overdue players
                            elif current_pick >= max_pick - 2:
                                # Approaching max_pick (within 2 picks) - increase urgency
                                distance_to_max = max_pick - current_pick
                                urgency_factor = 1 + (2 - distance_to_max) * 0.5  # Increasing urgency
                                prob = base_prob * urgency_factor
                            else:
                                # Within normal range - use base probability
                                prob = base_prob
                            
                            probabilities.append(prob)
                            player_ids.append(player_id)
                            player_names.append(player_name)
                        
                        # Normalize probabilities
                        total_prob = sum(probabilities)
                        if total_prob > 0:
                            probabilities = [p / total_prob for p in probabilities]
                            
                            # Select player using weighted random choice
                            selected_idx = np.random.choice(len(player_ids), p=probabilities)
                            selected_player_id = player_ids[selected_idx]
                            selected_player_name = player_names[selected_idx]
                            
                            # Mark player as drafted
                            if mark_player_drafted(draft_table, selected_player_id, selected_player_name):
                                # Clear DynamoDB cache since we just made a change
                                cache_key_drafted = f"drafted_players_{draft_session_id}"
                                if cache_key_drafted in st.session_state:
                                    del st.session_state[cache_key_drafted]
                                # Update session state
                                st.session_state[pick_key] = current_pick + 1
                                st.session_state[last_picked_key] = selected_player_name
                                st.success(f"✅ Pick {current_pick}: **{selected_player_name}** has been drafted!")
                                st.rerun()
                        else:
                            st.warning("Could not calculate probabilities. All players may be outside their draft range.")
                    else:
                        # No players with ADP data - select randomly from remaining
                        if len(undrafted_df) > 0:
                            selected_player = undrafted_df.sample(n=1).iloc[0]
                            selected_player_id = str(selected_player['id'])
                            selected_player_name = selected_player.get('name', 'Unknown')
                            
                            if mark_player_drafted(draft_table, selected_player_id, selected_player_name):
                                # Clear DynamoDB cache since we just made a change
                                cache_key_drafted = f"drafted_players_{draft_session_id}"
                                if cache_key_drafted in st.session_state:
                                    del st.session_state[cache_key_drafted]
                                st.session_state[pick_key] = current_pick + 1
                                st.session_state[last_picked_key] = selected_player_name
                                st.success(f"✅ Pick {current_pick}: **{selected_player_name}** has been drafted!")
                                st.rerun()
                        else:
                            st.warning("No undrafted players available!")
                else:
                    st.warning("ADP data not available for mock draft simulation.")
        
        # Show current pick and last picked for live drafts too
        if current_draft_type == "Live Draft":
            st.markdown("---")
            st.subheader("Draft Status")
            
            # Initialize current pick in session state (for tracking)
            pick_key = f"current_pick_{draft_session_id}_{format_type}"
            last_picked_key = f"last_picked_{draft_session_id}_{format_type}"
            
            if pick_key not in st.session_state:
                st.session_state[pick_key] = 1
            if last_picked_key not in st.session_state:
                st.session_state[last_picked_key] = None
            
            current_pick = st.session_state[pick_key]
            last_picked = st.session_state[last_picked_key]
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Current Pick", current_pick)
            with col2:
                if last_picked:
                    st.info(f"Last Picked: **{last_picked}**")
                else:
                    st.info("No picks yet")
        
        st.markdown("---")
        
        # TEAM STATS COMPARISON CHART
        # Get players on my team (use full dataset, not filtered - team stats should always show regardless of filters)
        my_team_df = df[df.get('My Team', False) == True].copy() if 'My Team' in df.columns else pd.DataFrame()
        
        if len(my_team_df) > 0:
            st.subheader("My Team Stats vs Percentiles")
            
            try:
                percentiles_df = load_percentiles(format_type, ATHENA_SCHEMA, ATHENA_S3_OUTPUT, ATHENA_REGION)
                
                if len(percentiles_df) > 0:
                    # Aggregate my team stats
                    # Map category names from percentiles to player stats columns
                    category_mapping = {
                        'R': 'r',
                        'HR': 'hr',
                        'RBI': 'rbi',
                        'SB': 'sb',
                        'AVG': 'avg',
                        'K': 'k',
                        'W': 'w',
                        'S': 'sv',  # Saves: S in percentiles, sv in rankings
                        'ERA': 'era',
                        'WHIP': 'whip'
                    }
                    
                    # Calculate aggregated team stats
                    team_stats = {}
                    for percentile_cat, stat_col in category_mapping.items():
                        if stat_col in my_team_df.columns:
                            if stat_col == 'avg':
                                # AVG = H / AB - need to aggregate properly
                                if 'ab' in my_team_df.columns:
                                    # Calculate total hits from avg and ab, then recalculate
                                    total_h = (my_team_df['ab'].fillna(0) * my_team_df['avg'].fillna(0)).sum()
                                    total_ab = my_team_df['ab'].fillna(0).sum()
                                    team_stats[percentile_cat] = total_h / total_ab if total_ab > 0 else 0
                                else:
                                    # Fallback to simple average if AB not available
                                    team_stats[percentile_cat] = my_team_df[stat_col].mean() if len(my_team_df) > 0 else 0
                            elif stat_col in ['era', 'whip']:
                                # For ERA and WHIP, aggregate as weighted average by IP
                                if 'ip' in my_team_df.columns:
                                    total_ip = my_team_df['ip'].fillna(0).sum()
                                    if total_ip > 0:
                                        # Weighted average: sum(stat * ip) / sum(ip)
                                        weighted_sum = (my_team_df[stat_col].fillna(0) * my_team_df['ip'].fillna(0)).sum()
                                        team_stats[percentile_cat] = weighted_sum / total_ip
                                    else:
                                        team_stats[percentile_cat] = my_team_df[stat_col].mean() if len(my_team_df) > 0 else 0
                                else:
                                    # Fallback to simple average if IP not available
                                    team_stats[percentile_cat] = my_team_df[stat_col].mean() if len(my_team_df) > 0 else 0
                            else:
                                # Counting stats (R, HR, RBI, SB, K, W, sv) - sum them
                                team_stats[percentile_cat] = my_team_df[stat_col].fillna(0).sum()
                    
                    # Define category order
                    category_order = ['R', 'HR', 'RBI', 'SB', 'AVG', 'K', 'W', 'SV', 'ERA', 'WHIP']
                    
                    # Create comparison chart data in the specified order
                    categories = []
                    team_values = []
                    p80_values = []
                    p90_values = []
                    
                    # Create a mapping from percentile category to display name
                    category_display_map = {'S': 'SV'}  # Map S to SV for display
                    
                    # Build data in the specified order
                    for cat in category_order:
                        # Check if this category exists in percentiles (handle S -> SV mapping)
                        percentile_cat = 'S' if cat == 'SV' else cat
                        if percentile_cat in team_stats:
                            # Use display name (SV instead of S)
                            categories.append(cat)
                            team_values.append(team_stats[percentile_cat])
                            
                            # Get percentile values
                            percentile_row = percentiles_df[percentiles_df['category'] == percentile_cat]
                            if len(percentile_row) > 0:
                                p80_values.append(percentile_row.iloc[0]['p80'])
                                p90_values.append(percentile_row.iloc[0]['p90'])
                            else:
                                p80_values.append(0)
                                p90_values.append(0)
                    
                    if len(categories) > 0:
                        # Create transposed comparison table (categories as columns, percentiles as rows)
                        # Initialize data structure for transposed table
                        p80_row = {}
                        p90_row = {}
                        team_row = {}
                        
                        for cat, team_val, p80_val, p90_val in zip(categories, team_values, p80_values, p90_values):
                            # Format values based on category type
                            if cat == 'AVG':
                                team_str = f'{team_val:.3f}'
                                p80_str = f'{p80_val:.3f}'
                                p90_str = f'{p90_val:.3f}'
                            elif cat in ['ERA', 'WHIP']:
                                team_str = f'{team_val:.2f}'
                                p80_str = f'{p80_val:.2f}'
                                p90_str = f'{p90_val:.2f}'
                            else:
                                team_str = f'{int(team_val)}'
                                p80_str = f'{int(p80_val)}'
                                p90_str = f'{int(p90_val)}'
                            
                            # Add to each row
                            p80_row[cat] = p80_str
                            p90_row[cat] = p90_str
                            team_row[cat] = team_str
                        
                        # Create transposed dataframe with categories as columns
                        comparison_data = [
                            {'Metric': '80th Percentile', **p80_row},
                            {'Metric': '90th Percentile', **p90_row},
                            {'Metric': 'My Team', **team_row}
                        ]
                        
                        comparison_df = pd.DataFrame(comparison_data)
                        
                        # Configure column widths to make them narrower
                        column_config = {
                            'Metric': st.column_config.TextColumn(
                                'Metric',
                                width='medium'
                            )
                        }
                        # Make all stat columns narrower
                        for cat in categories:
                            column_config[cat] = st.column_config.TextColumn(
                                cat,
                                width='small'
                            )
                        
                        st.dataframe(
                            comparison_df, 
                            use_container_width=True, 
                            hide_index=True,
                            column_config=column_config
                        )
                    else:
                        st.info("No matching categories found between team stats and percentiles.")
                else:
                    st.info("No percentile data found for the selected format.")
                    
            except Exception as e:
                st.warning(f"Error loading percentile data: {str(e)}")
        else:
            st.info("Add players to 'My Team' to see stats comparison.")
        
        st.markdown("---")
        
        # Add row limit option to reduce memory usage
        row_limit_key = f"row_limit_{format_type}"
        if row_limit_key not in st.session_state:
            st.session_state[row_limit_key] = 500  # Default to 500 rows
        
        col1, col2 = st.columns([3, 1])
        with col1:
            st.subheader(f"Results: {len(filtered_df)} players")
        with col2:
            row_limit = st.number_input(
                "Max Rows to Display",
                min_value=100,
                max_value=5000,
                value=st.session_state[row_limit_key],
                step=100,
                help="Limit displayed rows to reduce memory usage",
                key=row_limit_key
            )
        
        # Prepare dataframe for editing - only include specified columns in specified order
        desired_columns = [
            'rank', 'Drafted', 'My Team', 'id', 'name', 'team', 'pos', 'adp', 
            'min_pick', 'max_pick', 'rank_diff', 'projected_opening_day_status', 
            'value', 'pa', 'ab', 'r', 'hr', 'rbi', 'sb', 'avg', 'obp', 'slg', 
            'ip', 'k', 'w', 'sv', 'era', 'whip'
        ]
        
        # Only include columns that exist in the dataframe
        available_columns = [col for col in desired_columns if col in filtered_df.columns]
        display_df = filtered_df[available_columns].copy()
        
        # Limit the display dataframe only (not the underlying filtered_df used for charts/calculations)
        # This way charts, team stats, and filtering still work with all data
        original_count = len(display_df)
        if original_count > row_limit:
            display_df = display_df.head(row_limit)
            st.info(f"⚠️ Showing first {row_limit} of {original_count} filtered players in table. Charts and stats use all {original_count} players. Adjust 'Max Rows to Display' to see more.")
        
        # Apply rounding to numeric columns
        # Round to whole numbers
        whole_number_cols = ['pa', 'ab', 'r', 'hr', 'rbi', 'sb', 'ip', 'k', 'w', 'sv']
        for col in whole_number_cols:
            if col in display_df.columns:
                display_df[col] = display_df[col].round(0).astype('Int64')  # Int64 allows NaN
        
        # Round to 3 decimal places (.000)
        three_decimal_cols = ['avg', 'obp', 'slg']
        for col in three_decimal_cols:
            if col in display_df.columns:
                display_df[col] = display_df[col].round(3)
        
        # Round to 2 decimal places (.00)
        two_decimal_cols = ['era', 'whip']
        for col in two_decimal_cols:
            if col in display_df.columns:
                display_df[col] = display_df[col].round(2)
        
        # Format value as currency with cents
        if 'value' in display_df.columns:
            display_df['value'] = display_df['value'].apply(
                lambda x: f"${float(x):,.2f}" if pd.notna(x) and pd.notnull(x) else ""
            )
        
        # Use st.data_editor with column configuration
        # Both "Drafted" and "My Team" columns should be editable (as checkboxes)
        column_config = {}
        if 'Drafted' in display_df.columns:
            column_config['Drafted'] = st.column_config.CheckboxColumn(
                "Drafted",
                help="Check to mark player as drafted",
                default=False
            )
        if 'My Team' in display_df.columns:
            column_config['My Team'] = st.column_config.CheckboxColumn(
                "My Team",
                help="Check to mark player as drafted to my team (also marks as drafted)",
                default=False
            )
        
        # Create list of columns that should NOT be editable
        editable_columns = ['Drafted', 'My Team']
        disabled_columns = [col for col in display_df.columns if col not in editable_columns]
        
        # Display editable dataframe
        # When user changes a checkbox, edited_df will have the updated values
        edited_df = st.data_editor(
            display_df,
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
            disabled=disabled_columns  # Only Drafted columns are editable
        )
        
        # Check if any draft status changed and update DynamoDB
        # Note: We need to work with the original filtered_df for player lookups since display_df has formatted values
        if ('Drafted' in edited_df.columns or 'My Team' in edited_df.columns) and 'id' in edited_df.columns:
            # Create dictionaries of original statuses for comparison
            # Use filtered_df to get original values (before formatting)
            original_drafted = {}
            original_my_team = {}
            if 'Drafted' in filtered_df.columns and 'id' in filtered_df.columns:
                for _, row in filtered_df.iterrows():
                    player_id = str(row['id'])
                    # Match by id to get original status
                    original_drafted[player_id] = row['Drafted']
            if 'My Team' in filtered_df.columns and 'id' in filtered_df.columns:
                for _, row in filtered_df.iterrows():
                    player_id = str(row['id'])
                    original_my_team[player_id] = row['My Team']
            
            # Check each row in edited dataframe
            changes_made = False
            newly_drafted_players = []  # Track newly drafted players for "Last Picked" display
            
            for _, row in edited_df.iterrows():
                player_id = str(row['id'])
                player_name = row.get('name', 'Unknown')
                
                new_drafted = row.get('Drafted', False) if 'Drafted' in edited_df.columns else False
                new_my_team = row.get('My Team', False) if 'My Team' in edited_df.columns else False
                original_drafted_status = original_drafted.get(player_id, False)
                original_my_team_status = original_my_team.get(player_id, False)
                
                # Priority 1: Handle "My Team" changes
                # If checked, mark as my team AND as drafted
                # If unchecked, just unmark as my team (keep drafted status)
                if 'My Team' in edited_df.columns:
                    if new_my_team != original_my_team_status:
                        mark_player_to_my_team(draft_table, player_id, player_name, new_my_team)
                        changes_made = True
                        # If marking as my team, ensure drafted is also true
                        if new_my_team and not new_drafted:
                            mark_player_drafted(draft_table, player_id, player_name)
                            # Track if this is a newly drafted player
                            if not original_drafted_status:
                                newly_drafted_players.append(player_name)
                
                # Priority 2: Handle "Drafted" changes
                # If unchecked, also uncheck my team (can't be on my team if not drafted)
                if 'Drafted' in edited_df.columns:
                    if new_drafted != original_drafted_status:
                        if new_drafted:
                            mark_player_drafted(draft_table, player_id, player_name)
                            # Track if this is a newly drafted player
                            if not original_drafted_status:
                                newly_drafted_players.append(player_name)
                        else:
                            # If unchecking drafted, delete from DynamoDB (which removes both drafted and my team status)
                            mark_player_undrafted(draft_table, player_id)
                        changes_made = True
            
            # Update pick counter based on total drafted players (for both mock and live drafts)
            if changes_made:
                # Clear DynamoDB cache since we just made changes
                cache_key_drafted = f"drafted_players_{draft_session_id}"
                cache_key_my_team = f"my_team_players_{draft_session_id}"
                if cache_key_drafted in st.session_state:
                    del st.session_state[cache_key_drafted]
                if cache_key_my_team in st.session_state:
                    del st.session_state[cache_key_my_team]
                
                pick_key = f"current_pick_{draft_session_id}_{format_type}"
                last_picked_key = f"last_picked_{draft_session_id}_{format_type}"
                
                # Initialize pick counter if it doesn't exist
                if pick_key not in st.session_state:
                    st.session_state[pick_key] = 1
                if last_picked_key not in st.session_state:
                    st.session_state[last_picked_key] = None
                
                # Calculate pick counter based on total drafted players + 1
                # This ensures accuracy even if players are unchecked
                # We query DynamoDB to get the current count (force refresh to get latest)
                total_drafted_count = len(get_drafted_players(draft_table, draft_session_id, force_refresh=True))
                st.session_state[pick_key] = total_drafted_count + 1
                
                # Update last picked player (only if someone was just drafted)
                if newly_drafted_players:
                    st.session_state[last_picked_key] = newly_drafted_players[-1]
                # If someone was undrafted, we leave last_picked as is (we don't track draft order)
            
            # Only rerun if changes were actually made
            if changes_made:
                # Force refresh of drafted status by clearing cache or rerunning
                st.rerun()  # Refresh to show updated status
    
    elif page == "📈 ADP Chart":
        # ADP CHART PAGE
        st.markdown("---")
        st.subheader(f"ADP Chart: {len(filtered_df)} players")
        
        # Filter for number of players to show (ADP chart only)
        max_players_key = f"adp_chart_max_players_{format_type}"
        
        col1, col2 = st.columns([1, 1])
        with col1:
            max_players = st.number_input(
                "Number of Players to Show",
                min_value=10,
                max_value=1000,
                value=50,  # Default value (session state will override if it exists)
                step=10,
                help="Limit the chart to show only the top N players by rank",
                key=max_players_key
            )
            # Note: st.number_input with key automatically updates session_state, so we don't need to set it manually
        
        with col2:
            upcoming_pick = st.number_input(
                "My Upcoming Pick",
                min_value=1,
                max_value=1000,
                value=None,
                step=1,
                help="Enter your upcoming draft pick number to see a vertical line on the chart"
            )
        
        # Check if we have the required columns for the chart
        required_cols = ['name', 'pos', 'adp', 'min_pick', 'max_pick']
        missing_cols = [col for col in required_cols if col not in filtered_df.columns]
        
        if missing_cols:
            st.error(f"Missing required columns for chart: {', '.join(missing_cols)}")
        else:
            # Prepare data for chart - only include players with valid ADP data
            chart_df = filtered_df[
                filtered_df['adp'].notna() & 
                filtered_df['min_pick'].notna() & 
                filtered_df['max_pick'].notna()
            ].copy()
            
            if len(chart_df) == 0:
                st.warning("No players with ADP data available to display in chart.")
            else:
                # Sort by rank for better visualization
                if 'rank' in chart_df.columns:
                    chart_df = chart_df.sort_values('rank', ascending=True)
                else:
                    # Fallback to ADP if rank not available
                    chart_df = chart_df.sort_values('adp', ascending=True)
                
                # Limit to top N players based on filter
                chart_df = chart_df.head(max_players)
                
                # Create player labels with name and position
                chart_df['player_label'] = chart_df.apply(
                    lambda row: f"{row['name']} ({row['pos']})", axis=1
                )
                
                # Create the chart using plotly
                fig = go.Figure()
                
                # Add horizontal lines (whiskers) for min and max picks with ADP marker
                for idx, row in chart_df.iterrows():
                    player_label = row['player_label']
                    adp = row['adp']
                    min_pick = row['min_pick']
                    max_pick = row['max_pick']
                    
                    # Add line from min to max (whisker/range line)
                    fig.add_trace(go.Scatter(
                        x=[min_pick, max_pick],
                        y=[player_label, player_label],
                        mode='lines',
                        line=dict(color='lightblue', width=3),
                        showlegend=False,
                        hoverinfo='skip'
                    ))
                    
                    # Add marker for ADP (center point) - larger and more prominent
                    # Get rank if available
                    rank_text = ""
                    if 'rank' in row:
                        rank_value = row['rank']
                        if pd.notna(rank_value):
                            rank_text = f"Rank: {int(rank_value)}<br>"
                    
                    fig.add_trace(go.Scatter(
                        x=[adp],
                        y=[player_label],
                        mode='markers',
                        marker=dict(
                            size=10,
                            color='darkblue',
                            symbol='circle',
                            line=dict(color='white', width=1)
                        ),
                        name=player_label,
                        hovertemplate=f"<b>{player_label}</b><br>" +
                                     rank_text +
                                     f"ADP: {adp:.1f}<br>" +
                                     f"Min Pick: {min_pick:.0f}<br>" +
                                     f"Max Pick: {max_pick:.0f}<br>" +
                                     f"Range: {max_pick - min_pick:.0f} picks<extra></extra>",
                        showlegend=False
                    ))
                
                # Add vertical line for upcoming draft pick if specified
                if upcoming_pick is not None and pd.notna(upcoming_pick):
                    # Get the y-axis range from the player labels
                    y_values = chart_df['player_label'].tolist()
                    fig.add_vline(
                        x=upcoming_pick,
                        line_dash="dash",
                        line_color="red",
                        line_width=2,
                        annotation_text=f"Pick {int(upcoming_pick)}",
                        annotation_position="top",
                        annotation=dict(font_size=12, font_color="red")
                    )
                
                # Calculate height based on number of players - ensure readable spacing
                # Use enough height per player so names are visible
                chart_height = max(400, len(chart_df) * 25)  # 25px per player for readability
                
                # Update layout - balance between readability and spacing
                fig.update_layout(
                    title="ADP Chart (Min Pick - ADP - Max Pick)",
                    xaxis_title="Pick Number",
                    yaxis_title="Player (Position)",
                    height=chart_height,
                    hovermode='closest',
                    margin=dict(l=150, r=50, t=10, b=10),  # Minimal top/bottom margins
                    xaxis=dict(
                        autorange=True,
                        showgrid=True,
                        gridwidth=1,
                        gridcolor='black',
                        title_font=dict(color='black', size=12),  # Dark, readable axis title
                        tickfont=dict(color='black', size=11)  # Dark, readable tick labels
                    ),
                    yaxis=dict(
                        autorange="reversed",
                        showgrid=False,
                        tickfont=dict(color='black', size=11),  # Dark, readable font
                        title_font=dict(color='black', size=12),  # Dark, readable axis title
                        # Reduce padding at top and bottom
                        range=[-0.5, len(chart_df) - 0.5]  # Tight range around data points
                    ),
                    font=dict(size=11, color='black'),  # Dark, readable font
                    paper_bgcolor='white',
                    plot_bgcolor='white'
                )
                
                # Display the chart
                st.plotly_chart(
                    fig, 
                    use_container_width=True,
                    config={
                        'displayModeBar': True,
                        'displaylogo': False,
                        'modeBarButtonsToRemove': ['lasso2d', 'select2d'],
                        'scrollZoom': False,  # Enable scroll zoom for mobile
                        'responsive': True   # Make chart responsive
                    }
                )