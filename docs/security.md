# Security

Least-privilege notes for the Fantasy Baseball Platform. This is a hobby
lakehouse — controls aim for clear blast-radius boundaries, not compliance
theater.

Related tickets: [#145](https://github.com/danolen/fantasy-baseball-platform/issues/145)
(E1.1 IAM split), epic
[#154](https://github.com/danolen/fantasy-baseball-platform/issues/154).

## Actor → principal → permissions

| Actor | IAM principal | Allowed actions (summary) | Secret / auth |
|-------|---------------|---------------------------|---------------|
| Draft Streamlit | IAM user `streamlit-draft-tool` (path `/streamlit/`) | Athena query; Glue catalog read; S3 lakehouse read; Athena results prefix R/W; DynamoDB R/W on `fantasy_baseball_draft*` (CreateTable interim until #147) | Streamlit Secrets → that user's access keys only |
| In-season Streamlit | IAM user `streamlit-inseason-tool` | Same Athena/Glue/S3 as draft; **no** DynamoDB | Streamlit Secrets → that user's access keys only |
| GHA MPD ingest | OIDC role `github-actions-mpd-ingest` (name may vary) | `s3:PutObject` on `mapping/mpd_player_id_map/*` | GitHub Actions OIDC (no long-lived keys) |
| GHA dbt freshness | OIDC role `github-actions-dbt-source-freshness` | Athena/Glue read; lakehouse read; Athena results R/W | GitHub Actions OIDC |
| Prefect ingest flows | Prefect `AwsCredentials` block / future ECS task role | S3 put on vendor ingest prefixes; Secrets Manager read of `fantasy-baseball-platform` | Prefect block + AWS SM |
| Maintainer admin | Personal IAM user / SSO (break-glass) | Full account as needed | `~/.aws` / SSO — **never** paste into Streamlit |
| Cursor Cloud Agent | Integration token + optional fine-grained PAT | Git: feature branches / PRs; Issues via PAT | SM key `gh_pat_issue_and_script_work`; prefer no AWS admin on the agent VM |

Terraform that creates the Streamlit users:
[`terraform/streamlit_apps_iam/`](../terraform/streamlit_apps_iam/README.md).

GHA roles (do not widen in #145):
[`terraform/github_actions_mpd_ingest/`](../terraform/github_actions_mpd_ingest/README.md),
[`terraform/github_actions_dbt_freshness/`](../terraform/github_actions_dbt_freshness/README.md).

## Secrets inventory (names only)

| Location | Names | Used by |
|----------|-------|---------|
| AWS Secrets Manager `fantasy-baseball-platform` | `nfbc_*`, `ftn_*`, `fangraphs_cookie`, `razzball_cookie`, `gh_pat_issue_and_script_work` | Prefect flows; issue scripts |
| Streamlit Secrets `[default]` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `ATHENA_*`, `DYNAMODB_*` (draft) | Streamlit apps |
| Local `.env` (gitignored) | Same as Streamlit for local runs | Laptop |
| GitHub Actions variables | `AWS_GHA_*_ROLE_ARN`, `ATHENA_S3_OUTPUT` | Workflows (ARNs, not secret keys) |

Never commit secret **values**. Rotate Streamlit keys by creating a new
access key on the dedicated user, updating Streamlit, then deleting the old
key.

## Maintainer checklist — finish #145 after merge

1. `cd terraform/streamlit_apps_iam` → `terraform apply` (see module README).
2. `aws iam create-access-key` for each new user.
3. Update **both** Streamlit Cloud apps' secrets to the dedicated keys.
4. Confirm apps still load data; draft mark-as-drafted still works.
5. Remove admin access keys from Streamlit Secrets.
6. Optionally deactivate unused admin keys that existed only for Streamlit.

Until steps 3–5 are done, acceptance criterion “Streamlit Secrets no longer
use the admin access keys” remains open on the maintainer side.

## Follow-ups (other E1 tickets)

| Topic | Ticket |
|-------|--------|
| Remove draft `CreateTable` + set `allow_dynamodb_create_table = false` | #147 |
| Streamlit auth vs private-only decision | #148 |
| CI permissions / Dependabot / secret scanning | #149 |
| Tighter GHA OIDC trust | #150 |
| Expand this doc (rotation runbook, fuller matrix) | #151 |
| Agent GitHub PAT docs | #152 |
| Branch protection on `master` | #153 |
