# Terraform state backend (one-time bootstrap)

Terraform needs an **S3 bucket** (with versioning enabled) **before** any module can use a remote backend. This bucket is created once by hand (or with the AWS CLI below); it is **not** managed by the modules that depend on it.

State locking uses **S3-native lockfiles** (`use_lockfile = true` in `backend.hcl`; Terraform 1.10+). You do **not** need a DynamoDB table for that path.

## What you are creating

| Resource | Suggested name | Purpose |
|----------|----------------|---------|
| S3 bucket | `fbp-terraform-state` | Stores `terraform.tfstate` files + S3-native lock sidecars |
| Region | `us-east-1` | Same region as the lakehouse |

### Optional: DynamoDB lock table (legacy)

Older docs used `dynamodb_table` in the S3 backend; that argument is **deprecated** in current Terraform in favor of `use_lockfile`. If you already created `terraform-state-lock`, you can keep it unused or delete it after switching your `backend.hcl` to `use_lockfile = true` and running `terraform init -reconfigure` successfully.

Pick a globally unique bucket name if `fbp-terraform-state` is already taken (S3 bucket names are worldwide unique).

## Prerequisites

- AWS CLI configured with credentials that can create S3 buckets (and DynamoDB only if you still want the legacy table)
- Account ID noted for later (`aws sts get-caller-identity`)

## 1. Create the state bucket

```bash
export AWS_REGION=us-east-1
export STATE_BUCKET=fbp-terraform-state

aws s3api create-bucket \
  --bucket "$STATE_BUCKET" \
  --region "$AWS_REGION"

aws s3api put-bucket-versioning \
  --bucket "$STATE_BUCKET" \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
  --bucket "$STATE_BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-encryption \
  --bucket "$STATE_BUCKET" \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
  }'
```

## 2. (Optional, legacy) DynamoDB lock table

Skip this if you use `use_lockfile = true` in `backend.hcl` (recommended). Only create the table if you intentionally want deprecated DynamoDB locking:

```bash
export LOCK_TABLE=terraform-state-lock

aws dynamodb create-table \
  --table-name "$LOCK_TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$AWS_REGION"
```

Wait until the table status is `ACTIVE`:

```bash
aws dynamodb describe-table --table-name "$LOCK_TABLE" --region "$AWS_REGION" \
  --query 'Table.TableStatus' --output text
```

## 3. Next step

Continue with [../github_actions_mpd_ingest/README.md](../github_actions_mpd_ingest/README.md) to apply the GitHub Actions OIDC role and wire the workflow.
