# Terraform state backend (one-time bootstrap)

Terraform needs an S3 bucket and DynamoDB table **before** any module can use a remote backend. These resources are created once by hand (or with the AWS CLI below); they are **not** managed by the modules that depend on them.

## What you are creating

| Resource | Suggested name | Purpose |
|----------|----------------|---------|
| S3 bucket | `dn-terraform-state` | Stores `terraform.tfstate` files |
| DynamoDB table | `terraform-state-lock` | State locking (`LockID` string hash key) |
| Region | `us-east-1` | Same region as the lakehouse |

Use different names only if `dn-terraform-state` is already taken globally (S3 bucket names are worldwide unique).

## Prerequisites

- AWS CLI configured with credentials that can create S3 buckets and DynamoDB tables
- Account ID noted for later (`aws sts get-caller-identity`)

## 1. Create the state bucket

```bash
export AWS_REGION=us-east-1
export STATE_BUCKET=dn-terraform-state

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

## 2. Create the lock table

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
