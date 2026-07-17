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

## CI supply-chain controls (#149)

| Control | Where | Notes |
|---------|-------|-------|
| Least-privilege `GITHUB_TOKEN` | `.github/workflows/ci.yml` → `permissions: contents: read` | Lint/parse/secret-scan jobs cannot push or publish packages with the default token. |
| Dependabot | `.github/dependabot.yml` | Weekly PRs for `pip` (`requirements*.txt`) and `github-actions`. |
| Secret scanning (CI) | `secret-scan` job in `ci.yml` | Runs [gitleaks](https://github.com/gitleaks/gitleaks) on every PR / `master` push. Uses the binary (not `gitleaks-action`) so private repos need no paid license. |
| Secret scanning (GitHub) | Repo **Settings → Code security** | Enable **Secret scanning** (and push protection if offered) so GitHub also blocks known token patterns on push. Free for public repos; available on private personal repos for standard secret scanning. |

A fake `AKIA...` / private-key style string committed in a PR should fail the
`secret-scan` job (and/or GitHub push protection if enabled).

## Streamlit app access model (#148)

**Decision (2026-07): option 2 — URL-obscured / private-only.**

The draft and in-season Streamlit Community Cloud apps are **not** behind
login today. Anyone who has the app URL can use it, and every page load that
hits Athena/DynamoDB runs as the dedicated app IAM user
(`streamlit-draft-tool` or `streamlit-inseason-tool`).

| Residual risk | Mitigation in place |
|---------------|---------------------|
| Leaked or guessed Streamlit URL | Do not post the URL publicly; treat a leaked URL like a credential incident |
| Stranger burns Athena / reads marts | Dedicated least-privilege IAM users (#145); no admin keys in Streamlit Secrets |
| Draft DynamoDB writes (when draft app is deployed) | Scoped to draft table prefix only |

This is an explicit trade-off for a solo hobby deployment (low ops, free-tier
single app). It is **not** “the app is private” in a security sense — only
obscured.

**Upgrade path:** require Streamlit Cloud authentication before sharing the
URL or treating the apps as multi-user — tracked in
[#166](https://github.com/danolen/fantasy-baseball-platform/issues/166).

## GitHub Actions OIDC trust (#150)

**Decision (2026-07): keep branch-scoped trust; defer GitHub Environments.**

| Role | Trust `sub` (exact `StringEquals`) | Permissions (unchanged / single-purpose) |
|------|--------------------------------------|------------------------------------------|
| MPD ingest | `repo:danolen/fantasy-baseball-platform:ref:refs/heads/master` | `s3:PutObject` on `mapping/mpd_player_id_map/*` only |
| dbt freshness | same branch subject | Athena/Glue read + lakehouse read + Athena results prefix R/W |

**Why not Environments yet**

- Solo maintainer; AWS workflows already require merge to `master` (fork PRs /
  feature branches cannot assume these roles).
- GitHub Environment **required reviewers** would pause **cron** /
  `schedule` jobs until someone approves — a poor fit for weekly MPD +
  daily freshness until we want that friction.
- Environments *without* reviewers add only a weak gate (an attacker who
  can merge to `master` can also add `environment: production` to a
  workflow). Real value comes with reviewers or when collaborators join.

**What we did tighten**

- OIDC `sub` conditions use `StringEquals` (not `StringLike`) so the
  subject cannot be widened with wildcards later by accident.

**Upgrade path:** environment-scoped OIDC + `environment: production` on
the AWS workflows — tracked in
[#168](https://github.com/danolen/fantasy-baseball-platform/issues/168).

After merging Terraform changes for this ticket, run `terraform apply` in
`terraform/github_actions_mpd_ingest` and
`terraform/github_actions_dbt_freshness` so IAM trust documents pick up
`StringEquals` (permissions are unchanged).

## Follow-ups (other E1 tickets)

| Topic | Ticket |
|-------|--------|
| Remove draft `CreateTable` + set `allow_dynamodb_create_table = false` | #147 (deferred until draft app redeploy) |
| **Enable Streamlit Cloud authentication (option 1)** | **#166** (follow-up to #148) |
| **Adopt GitHub Environments for GHA OIDC** | **#168** (follow-up to #150) |
| Expand this doc (rotation runbook, fuller matrix) | #151 |
| Agent GitHub PAT docs | #152 |
| Branch protection on `master` | #153 |
