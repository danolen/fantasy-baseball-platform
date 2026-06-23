# GitHub Actions: dbt source freshness (Terraform)

Creates a **read-only** IAM role that GitHub Actions assumes via **OIDC** (no long-lived AWS keys). The role can run `dbt source freshness` against Athena external sources: Glue catalog read, lakehouse S3 read, and read/write on the Athena query-results prefix only.

Workflow: `.github/workflows/dbt-source-freshness.yml`

## Manual steps (order matters)

### A. One-time: Terraform state backend

If you have not created the remote state bucket yet, follow **[../bootstrap/README.md](../bootstrap/README.md)** first.

### B. Apply this module

```bash
cd terraform/github_actions_dbt_freshness

cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars
# Edit athena_results_prefix if your ATHENA_S3_OUTPUT path differs.

terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

Save outputs:

```bash
terraform output -raw github_actions_role_arn
terraform output -raw athena_s3_output
```

**If `terraform apply` fails** because the GitHub OIDC provider already exists (e.g. from MPD ingest):

```hcl
create_github_oidc_provider = false
github_oidc_provider_arn    = "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
```

### C. Wire GitHub Actions (repository variables)

GitHub → **Settings → Secrets and variables → Actions → Variables**:

| Name | Value |
|------|--------|
| `AWS_GHA_DBT_FRESHNESS_ROLE_ARN` | `terraform output -raw github_actions_role_arn` |
| `ATHENA_S3_OUTPUT` | Must match `athena_results_prefix` exactly (full `s3://` URI with trailing slash). Use `terraform output -raw athena_s3_output` after apply. |

### D. Merge the workflow and test

1. Merge the PR that adds `.github/workflows/dbt-source-freshness.yml` to `master`.
2. **Actions → dbt source freshness → Run workflow** (`workflow_dispatch`).
3. Confirm the job passes (or WARN only on intentionally stale weekend sources).

## Schedule

The workflow runs **daily at 14:00 UTC** (`cron: 0 14 * * *`), approximately **10:00 AM US Eastern** during daylight saving time (~2 hours after the 8 AM ET Prefect ingest runs).

## What Terraform manages vs. not

| Managed here | Not managed here |
|--------------|------------------|
| GitHub OIDC provider (optional) | Lakehouse bucket `dn-lakehouse-dev` |
| IAM role + read/query policy | Glue external table definitions |
| | Full AWS import (#54) |

## Destroy

```bash
terraform destroy
```
