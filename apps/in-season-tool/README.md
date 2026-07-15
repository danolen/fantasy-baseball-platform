# Fantasy Baseball In-Season Tool

Streamlit app for FAAB worksheets and weekly lineup recommendations. Reads
marts from Athena; does **not** write to DynamoDB.

## AWS credentials

Use the dedicated IAM user `streamlit-inseason-tool` (Athena/Glue/S3 read for
query results only). Do **not** use maintainer admin keys or the draft-tool
user.

See [`docs/security.md`](../../docs/security.md) and
[`terraform/streamlit_apps_iam/`](../../terraform/streamlit_apps_iam/README.md).

### Local

```bash
# Repo root .env (gitignored)
ATHENA_SCHEMA=dbt_main
ATHENA_REGION=us-east-1
ATHENA_S3_OUTPUT=s3://YOUR_LAKEHOUSE_BUCKET/athena-results/
AWS_ACCESS_KEY_ID=...       # streamlit-inseason-tool
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

```bash
source venv/bin/activate
streamlit run apps/in-season-tool/app.py --server.port 8502
```

### Streamlit Cloud secrets

```toml
[default]
ATHENA_SCHEMA = "dbt_main"
ATHENA_REGION = "us-east-1"
ATHENA_S3_OUTPUT = "s3://YOUR_LAKEHOUSE_BUCKET/athena-results/"
AWS_ACCESS_KEY_ID = "..."
AWS_SECRET_ACCESS_KEY = "..."
AWS_DEFAULT_REGION = "us-east-1"
```

`ATHENA_S3_OUTPUT` must match `terraform output -raw athena_s3_output` from
`terraform/streamlit_apps_iam`.
