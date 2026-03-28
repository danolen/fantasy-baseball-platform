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

```bash
aws configure
```

This stores credentials in `~/.aws/credentials`. Alternatively, set environment variables:
```bash
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
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
3. AWS access keys with permissions for Athena, S3, and DynamoDB

### Configure Streamlit Secrets

In the Streamlit Cloud dashboard, go to your app settings and add secrets in TOML format:

```toml
[default]
ATHENA_DATABASE = "AwsDataCatalog"
ATHENA_SCHEMA = "dbt_main"
ATHENA_REGION = "us-east-1"
ATHENA_S3_OUTPUT = "s3://your-bucket/query-results/"

DYNAMODB_REGION = "us-east-1"
DYNAMODB_TABLE_NAME = "fantasy_baseball_draft"

AWS_ACCESS_KEY_ID = "your-access-key-id"
AWS_SECRET_ACCESS_KEY = "your-secret-access-key"
AWS_DEFAULT_REGION = "us-east-1"
```

AWS credentials are **required** for Streamlit Cloud -- the app needs them to connect to Athena, S3, and DynamoDB.

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
- Verify AWS credentials
- Check IAM permissions: `athena:StartQueryExecution`, `athena:GetQueryExecution`, `athena:GetQueryResults`, `s3:GetObject`, `dynamodb:*`

**"Module not found" errors**
- Ensure `requirements.txt` at the repo root includes all dependencies

---

## Security

- Never commit secrets to git (`.env` is in `.gitignore`)
- Use IAM roles when running on AWS infrastructure
- Grant only the minimum required AWS permissions
- Rotate credentials regularly

## Cost

- **Streamlit Cloud**: Free tier available
- **AWS Athena**: ~$5 per TB scanned
- **AWS DynamoDB**: Free tier includes 25GB storage and 25 read/write units
- **AWS S3**: Minimal cost for query results
