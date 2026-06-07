# GitHub Actions: MPD player ID map ingest (Terraform)

Creates an IAM role that GitHub Actions assumes via **OIDC** (no long-lived AWS keys in GitHub or git). The role can upload (overwrite) the Smart Fantasy Baseball player ID map CSV at:

`s3://dn-lakehouse-dev/mapping/mpd_player_id_map/SFBB Player ID Map - PLAYERIDMAP.csv`

The Python ingest script and workflow live in the repo root:

- `utils/ingest/mpd_player_map.py`
- `.github/workflows/ingest-mpd-player-map.yml`

## Manual steps (order matters)

### A. One-time: Terraform state backend

If you have not created the remote state bucket yet, follow **[../bootstrap/README.md](../bootstrap/README.md)** first. Use **`use_lockfile = true`** in `backend.hcl` (see `backend.hcl.example`); do not set `dynamodb_table` unless you are intentionally on the deprecated path.

If you already use `dynamodb_table` in `backend.hcl`, switch to `use_lockfile = true`, remove `dynamodb_table`, then run `terraform init -reconfigure -backend-config=backend.hcl` once from this directory.

### B. Apply this module

From this directory:

```bash
cd terraform/github_actions_mpd_ingest

cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars
# Edit backend.hcl / terraform.tfvars if your bucket or repo names differ.

terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

Save the role ARN:

```bash
terraform output -raw github_actions_role_arn
```

**If `terraform apply` fails** because the GitHub OIDC provider already exists in your account:

1. Find the ARN:  
   `aws iam list-open-id-connect-providers`
2. Set in `terraform.tfvars`:
   ```hcl
   create_github_oidc_provider = false
   github_oidc_provider_arn    = "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
   ```
3. Run `terraform apply` again.

### C. Wire GitHub Actions (repository variable)

In GitHub: **Settings → Secrets and variables → Actions → Variables → New repository variable**

| Name | Value |
|------|--------|
| `AWS_GHA_MPD_INGEST_ROLE_ARN` | Output of `terraform output -raw github_actions_role_arn` |

This is not a secret; it is the role ARN Terraform created.

### D. Merge the workflow and test

1. Merge the PR that adds `.github/workflows/ingest-mpd-player-map.yml` to `master` (the IAM trust policy only allows that branch).
2. **Actions → Ingest MPD player ID map → Run workflow** (`workflow_dispatch`).
3. Confirm the object in S3:

   ```bash
   aws s3 ls "s3://dn-lakehouse-dev/mapping/mpd_player_id_map/"
   ```

### E. Optional: local dry run

From the repo root with AWS credentials that can `s3:PutObject` on the prefix:

```bash
source venv/bin/activate
pip install -r requirements.txt
python utils/ingest/mpd_player_map.py --dry-run
python utils/ingest/mpd_player_map.py
```

## Schedule

The workflow runs **every Sunday at 10:00 UTC** (`cron: 0 10 * * 0`), which is approximately **6:00 AM US Eastern** during daylight saving time.

## What Terraform manages vs. not

| Managed here | Not managed here (existing / other tickets) |
|--------------|---------------------------------------------|
| GitHub OIDC provider (optional) | S3 lakehouse bucket `dn-lakehouse-dev` |
| IAM role + S3 upload policy | Glue/Athena external table for `mapping.player_id_map` |
| | Full AWS import (#54) |

## Destroy

```bash
terraform destroy
```

Only removes the role and policy (and the OIDC provider if this module created it). It does **not** delete lakehouse data or the state backend bucket.
