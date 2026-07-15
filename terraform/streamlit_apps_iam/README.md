# Streamlit app IAM users (Terraform)

Creates **two dedicated IAM users** for Streamlit Community Cloud so the apps
no longer share the maintainer's admin access keys.

| User (default name) | App | Permissions |
|---------------------|-----|-------------|
| `streamlit-draft-tool` | Draft tool | Athena/Glue read, lakehouse S3 read, Athena results R/W, DynamoDB on `fantasy_baseball_draft*` |
| `streamlit-inseason-tool` | In-season tool | Athena/Glue read, lakehouse S3 read, Athena results R/W only |

**Access keys are not created by Terraform** (avoids secrets in state). Create
them with the AWS CLI after apply and paste into Streamlit Secrets.

Ticket: [#145](https://github.com/danolen/fantasy-baseball-platform/issues/145)

## Manual steps (order matters)

### A. One-time: Terraform state backend

If you have not created the remote state bucket yet, follow
**[../bootstrap/README.md](../bootstrap/README.md)** first.

### B. Apply this module

```bash
cd terraform/streamlit_apps_iam

cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars
# Edit athena_results_prefix if ATHENA_S3_OUTPUT differs.

terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

Confirm outputs:

```bash
terraform output
```

### C. Create access keys (once per user)

```bash
aws iam create-access-key --user-name streamlit-draft-tool
aws iam create-access-key --user-name streamlit-inseason-tool
```

Save `AccessKeyId` / `SecretAccessKey` somewhere temporary (password manager).
You will paste them into Streamlit and then discard the plaintext.

### D. Update Streamlit Community Cloud secrets

For **each** app, replace any maintainer/admin `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` with the matching dedicated user keys.

Draft tool (`apps/draft-tool/app.py`):

```toml
[default]
ATHENA_DATABASE = "AwsDataCatalog"
ATHENA_SCHEMA = "dbt_main"
ATHENA_REGION = "us-east-1"
ATHENA_S3_OUTPUT = "s3://YOUR_LAKEHOUSE_BUCKET/athena-results/"

DYNAMODB_REGION = "us-east-1"
DYNAMODB_TABLE_NAME = "fantasy_baseball_draft"

AWS_ACCESS_KEY_ID = "AKIA..."      # streamlit-draft-tool only
AWS_SECRET_ACCESS_KEY = "..."
AWS_DEFAULT_REGION = "us-east-1"
```

In-season tool (`apps/in-season-tool/app.py`):

```toml
[default]
ATHENA_SCHEMA = "dbt_main"
ATHENA_REGION = "us-east-1"
ATHENA_S3_OUTPUT = "s3://YOUR_LAKEHOUSE_BUCKET/athena-results/"

AWS_ACCESS_KEY_ID = "AKIA..."      # streamlit-inseason-tool only
AWS_SECRET_ACCESS_KEY = "..."
AWS_DEFAULT_REGION = "us-east-1"
```

`ATHENA_S3_OUTPUT` **must** match `athena_results_prefix` from this module
(`terraform output -raw athena_s3_output`).

### E. Verify

1. Open each Streamlit app — rankings / FAAB load without Access Denied.
2. Draft tool: mark a player drafted (DynamoDB write).
3. In AWS IAM: confirm the old admin access key is **not** present in either
   app's Streamlit Secrets.
4. Optionally deactivate/delete unused admin access keys that were only used
   for Streamlit.

### F. After #147 (E1.2)

When runtime `CreateTable` is removed from the draft app:

```hcl
allow_dynamodb_create_table = false
```

Then `terraform apply` again.

## What this module does **not** manage

| Not here | Where |
|----------|--------|
| GitHub Actions OIDC roles | `terraform/github_actions_*` (unchanged by #145) |
| Prefect task role / AwsCredentials block | Prefect / future `terraform/prefect/` |
| Maintainer admin user | Your personal IAM user / SSO (break-glass only) |
| DynamoDB table resources | Manual / #147 / full import #54 |
| Access key secret values | CLI + Streamlit Secrets / password manager |

## Destroy

```bash
terraform destroy
```

Deactivate Streamlit secrets first. Destroy removes the IAM users and
inline policies; it does not delete lakehouse data.
