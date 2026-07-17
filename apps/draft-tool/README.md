# Fantasy Baseball Draft Tool

A Streamlit web app for viewing player rankings and tracking draft picks in real time. Connects to Amazon Athena for analytical data and Amazon DynamoDB for draft state.

## Features

- Player rankings and valuations for various fantasy baseball contest formats
- Projected player stats
- Real-time filtering and sorting
- Track drafted/undrafted players with one click (persisted in DynamoDB)
- Mobile- and desktop-friendly
- Deployable to Streamlit Community Cloud

---

## Local Setup

### 1. Create and activate a virtual environment

**macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows:**
```bash
python3 -m venv venv
venv\Scripts\activate
```

Or use the setup script from the repo root:
```bash
./setup.sh
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

(`requirements.txt` lives at the repo root because Streamlit Cloud requires it there.)

### 3. Configure AWS credentials

Use the **dedicated** draft-tool IAM user (`streamlit-draft-tool`), not your
personal admin keys. See [`docs/security.md`](../../docs/security.md) and
[`terraform/streamlit_apps_iam/`](../../terraform/streamlit_apps_iam/README.md).

```bash
aws configure
# or:
export AWS_ACCESS_KEY_ID=your_draft_tool_key
export AWS_SECRET_ACCESS_KEY=your_draft_tool_secret
export AWS_DEFAULT_REGION=us-east-1
```

### 4. Configure the app

Create a `.env` file at the repo root:
```bash
ATHENA_DATABASE=AwsDataCatalog
ATHENA_SCHEMA=dbt_main
ATHENA_REGION=us-east-1
ATHENA_S3_OUTPUT=s3://your-bucket/query-results/
DYNAMODB_REGION=us-east-1
DYNAMODB_TABLE_NAME=fantasy_baseball_draft
```

The app loads `.env` automatically via `python-dotenv`. You can also set these as regular environment variables -- they take precedence over `.env` values.

**Config priority:** Streamlit Secrets > environment variables > `.env` file > defaults

### 5. Run the app

```bash
streamlit run apps/draft-tool/app.py
```

---

## Deployment to Streamlit Community Cloud

### Prerequisites

1. GitHub account with this repo pushed
2. Streamlit account at [share.streamlit.io](https://share.streamlit.io)
3. Access keys for the dedicated `streamlit-draft-tool` IAM user (not admin)

### Configure Streamlit Secrets

In the Streamlit Cloud dashboard, go to your app settings and add secrets in TOML format.
Use keys from `streamlit-draft-tool` only — never paste maintainer admin keys.

```toml
[default]
ATHENA_DATABASE = "AwsDataCatalog"
ATHENA_SCHEMA = "dbt_main"
ATHENA_REGION = "us-east-1"
ATHENA_S3_OUTPUT = "s3://YOUR_LAKEHOUSE_BUCKET/athena-results/"

DYNAMODB_REGION = "us-east-1"
DYNAMODB_TABLE_NAME = "fantasy_baseball_draft"

AWS_ACCESS_KEY_ID = "your-draft-tool-access-key-id"
AWS_SECRET_ACCESS_KEY = "your-draft-tool-secret-access-key"
AWS_DEFAULT_REGION = "us-east-1"
```

AWS credentials are **required** for Streamlit Cloud -- the app needs them to connect to Athena, S3, and DynamoDB.
`ATHENA_S3_OUTPUT` must match the prefix allowed by `terraform/streamlit_apps_iam`.

### Deploy

1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Click **New app** and connect your GitHub repository
3. Set **Main file path** to `apps/draft-tool/app.py`
4. Choose your branch (e.g., `main`)
5. Click **Deploy**

### Verify

- App loads without errors
- Player rankings load from Athena
- Draft tracking works (mark a player as drafted)

### Updating

Push changes to GitHub and Streamlit Cloud redeploys automatically.

---

## Troubleshooting

**"Configuration Error: ATHENA_S3_OUTPUT is required"**
- Add `ATHENA_S3_OUTPUT` to your `.env` (local) or Streamlit Secrets (cloud)

**"Access Denied" or AWS authentication errors**
- Verify you are using the `streamlit-draft-tool` keys (not admin)
- Check IAM: Athena query + Glue read + lakehouse/results S3 + DynamoDB on `fantasy_baseball_draft*` (see `terraform/streamlit_apps_iam`)

**"Module not found" errors**
- Ensure `requirements.txt` at the repo root includes all dependencies

---

## Security

- Never commit secrets to git (`.env` is in `.gitignore`)
- Use the dedicated `streamlit-draft-tool` IAM user — not maintainer admin keys
- Grant only the minimum required AWS permissions (`docs/security.md`)
- Rotate Streamlit access keys on that user regularly
- **Access model:** Streamlit Cloud apps are URL-obscured only (no login) per
  [#148](https://github.com/danolen/fantasy-baseball-platform/issues/148) —
  do not publish the app URL; upgrade to auth is
  [#166](https://github.com/danolen/fantasy-baseball-platform/issues/166)

## Cost

- **Streamlit Cloud**: Free tier available
- **AWS Athena**: ~$5 per TB scanned
- **AWS DynamoDB**: Free tier includes 25GB storage and 25 read/write units
- **AWS S3**: Minimal cost for query results
