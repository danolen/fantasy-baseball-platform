# Security

Least-privilege notes for the Fantasy Baseball Platform. This is a hobby
lakehouse — controls aim for clear blast-radius boundaries, not compliance
theater.

Related: epic [#154](https://github.com/danolen/fantasy-baseball-platform/issues/154).

| Section | Purpose |
|---------|---------|
| [Actor matrix](#actor--principal--permissions) | Who may do what in AWS / GitHub |
| [Secrets inventory](#secrets-inventory-names-only) | Where secret *names* live (never values) |
| [Agent GitHub access](#agent-github-access) | Cursor integration token vs fine-grained PAT |
| [Rotation checklist](#rotation-checklist) | How to rotate keys, PAT, vendor cookies |
| [Streamlit access](#streamlit-app-access-model-148) | Private-only decision |
| [GHA OIDC trust](#github-actions-oidc-trust-150) | Branch-scoped trust decision |
| [Branch protection](#branch-protection-on-master-153) | Ruleset + maintainer bypass |
| [CI supply chain](#ci-supply-chain-controls-149) | Dependabot + gitleaks |

---

## Actor → principal → permissions

| Actor | IAM / GitHub principal | Allowed actions (summary) | Auth material |
|-------|------------------------|---------------------------|---------------|
| Draft Streamlit | IAM user `streamlit-draft-tool` (`/streamlit/`) | Athena query; Glue catalog read; S3 lakehouse read; Athena results prefix R/W; DynamoDB R/W on `fantasy_baseball_draft*` (CreateTable interim until #147) | Streamlit Secrets → that user's access keys only |
| In-season Streamlit | IAM user `streamlit-inseason-tool` | Same Athena/Glue/S3 as draft; **no** DynamoDB | Streamlit Secrets → that user's access keys only |
| GHA MPD ingest | OIDC role (default name `github-actions-mpd-player-map-ingest`) | `s3:PutObject` on `mapping/mpd_player_id_map/*` only | GitHub Actions OIDC (no long-lived AWS keys) |
| GHA dbt freshness | OIDC role (default name `github-actions-dbt-source-freshness`) | Athena/Glue read; lakehouse read; Athena results prefix R/W | GitHub Actions OIDC |
| Prefect ingest flows | Prefect `AwsCredentials` block / future ECS task role | S3 put on vendor ingest prefixes; `secretsmanager:GetSecretValue` on `fantasy-baseball-platform` | Prefect block + AWS SM |
| Maintainer admin | Personal IAM user / SSO (break-glass) | Full account as needed for Terraform, IAM, debugging | `~/.aws` / SSO — **never** paste into Streamlit or agent secrets |
| Cursor Cloud Agent | GitHub App installation token + optional fine-grained PAT | Feature branches / PRs (integration); Issues create/label/edit via PAT | See [Agent GitHub access](#agent-github-access) |

Terraform:

- Streamlit users: [`terraform/streamlit_apps_iam/`](../terraform/streamlit_apps_iam/README.md)
- GHA MPD: [`terraform/github_actions_mpd_ingest/`](../terraform/github_actions_mpd_ingest/README.md)
- GHA freshness: [`terraform/github_actions_dbt_freshness/`](../terraform/github_actions_dbt_freshness/README.md)

Do **not** widen GHA or Streamlit policies without updating this matrix in the same PR.

---

## Secrets inventory (names only)

Never commit secret **values**. Names and locations only.

### AWS Secrets Manager — `fantasy-baseball-platform` (`us-east-1`)

| JSON key | Used by |
|----------|---------|
| `nfbc_liu`, `nfbc_jwt` | Prefect NFBC flows |
| `ftn_access_token`, `ftn_refresh_token`, `ftn_user_id` | Prefect FTN flows |
| `fangraphs_cookie` | Prefect FanGraphs flows |
| `razzball_cookie` | Prefect Razzball flows |
| `gh_pat_issue_and_script_work` | Issue scripts / agents (fine-grained GitHub PAT) |

Aliases still accepted by `scripts/create_planning_issues.py` for the PAT:
`token`, `GH_PAT`, `gh_pat`. Prefer `gh_pat_issue_and_script_work`.

### Streamlit Community Cloud — app Secrets `[default]`

| Name | Draft | In-season |
|------|-------|-----------|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `streamlit-draft-tool` only | `streamlit-inseason-tool` only |
| `AWS_DEFAULT_REGION` | yes | yes |
| `ATHENA_*` (e.g. `ATHENA_S3_OUTPUT`, schema) | yes | yes |
| `DYNAMODB_*` | yes | no |

### Local (gitignored)

| Location | Contents |
|----------|----------|
| Repo-root `.env` | Same names as Streamlit for local app / dbt runs |
| `~/.aws/credentials` | Maintainer admin or named profiles — not committed |

### GitHub Actions (variables, not secrets)

| Variable | Purpose |
|----------|---------|
| `AWS_GHA_MPD_INGEST_ROLE_ARN` | OIDC role for MPD upload |
| `AWS_GHA_DBT_FRESHNESS_ROLE_ARN` | OIDC role for freshness |
| `ATHENA_S3_OUTPUT` | Results URI for freshness workflow |

Role ARNs are identifiers, not credentials.

---

## Agent GitHub access

Cursor Cloud Agents may see **two** different GitHub credential paths. Treat
them separately — do not assume laptop `gh auth` matches the Cloud Agent.

| Credential | Typical form | Create issue | Label / edit / comment / close | Push branch / open PR |
|------------|--------------|--------------|--------------------------------|------------------------|
| Cursor GitHub App installation token (Cloud Agent default) | `ghs_…` | Yes | **No** (403) | Yes (git remote) |
| Fine-grained PAT (Secrets Manager) | `github_pat_…` | Yes | **Yes** | Only if Contents/PR scopes granted (not required for issue scripts) |
| Maintainer laptop `gh auth` | user OAuth / PAT | Yes | Yes (if scoped) | Yes |

**PAT for issue scripts (required setup):**

- AWS Secrets Manager secret: `fantasy-baseball-platform` (`us-east-1`)
- JSON key: `gh_pat_issue_and_script_work`
- Repo access: **only** `danolen/fantasy-baseball-platform`
- Scopes: **Issues** read/write, **Metadata** read-only
- No Administration / Secrets / Workflows; **not** a classic `repo`-scoped `ghp_…` PAT

**Resolution order** (`scripts/create_planning_issues.py` and
`scripts/verify_gh_issue_pat.py`): Secrets Manager (preferred on agents) →
`GH_PAT` / `GH_TOKEN` env → ambient `gh auth`.

### Verify (re-run anytime)

```sh
# AWS creds that can read the secret, then:
python scripts/verify_gh_issue_pat.py
```

Expected: `PASS` after create+label, comment, and close of a throwaway issue.
Verified 2026-07-16 (`scripts/verify_gh_issue_pat.py` → probe #173) as part of #152.

Details: [`scripts/README.md`](../scripts/README.md).

**AWS on agent VMs:** prefer no admin keys. Default to repo-only work; AWS is
maintainer-applied per `AGENTS.md` unless the ticket says otherwise.

---

## Rotation checklist

### Streamlit IAM access keys

1. `aws iam create-access-key --user-name streamlit-inseason-tool` (or `streamlit-draft-tool`).
2. Update Streamlit Cloud Secrets with the new key pair.
3. Confirm the app loads (Athena query succeeds).
4. `aws iam delete-access-key --user-name … --access-key-id <old>`.
5. If the Streamlit **URL** leaked: rotate keys **and** treat the URL as public
   until auth lands (#166).

### Fine-grained GitHub PAT (`gh_pat_issue_and_script_work`)

1. GitHub → Settings → Developer settings → Fine-grained tokens → generate a
   new token with the scopes above; set an expiration (e.g. 90 days).
2. Update Secrets Manager JSON key `gh_pat_issue_and_script_work` (do not
   commit the value).
3. Revoke the old token in GitHub.
4. Smoke: `GH_TOKEN=… gh label list --repo danolen/fantasy-baseball-platform --limit 1`
   or run `python scripts/create_planning_issues.py --dry-run` with SM creds.

### Vendor cookies / tokens (NFBC, FTN, FanGraphs, Razzball)

1. Re-copy fresh session values from the vendor UI / browser (or refresh flow).
2. Update only the relevant keys inside `fantasy-baseball-platform` in Secrets
   Manager.
3. Re-run the affected Prefect flow (`--dry-run` first if available).
4. Do not store vendor cookies in git, Streamlit Secrets, or Cursor dashboard
   secrets.

### After rotating anything

- Update calendar / password-manager note with the new expiration (PAT) or
  “rotated on DATE” — store a **pointer**, not a pile of old key material.

---

## CI supply-chain controls (#149)

| Control | Where | Notes |
|---------|-------|-------|
| Least-privilege `GITHUB_TOKEN` | `.github/workflows/ci.yml` → `permissions: contents: read` | Lint/parse/secret-scan cannot push or publish with the default token |
| Dependabot | `.github/dependabot.yml` | Weekly PRs for `pip` and `github-actions` |
| Secret scanning (CI) | `secret-scan` job in `ci.yml` | [gitleaks](https://github.com/gitleaks/gitleaks) binary (private-repo friendly) |
| Secret scanning (GitHub) | Repo **Settings → Code security** | Enable Secret scanning + push protection if offered |

High-entropy AWS-style strings in a PR should fail `secret-scan`. AWS docs
`EXAMPLE` keys and low-entropy placeholders may be ignored by gitleaks.

---

## Streamlit app access model (#148)

**Decision (2026-07): option 2 — URL-obscured / private-only.**

Apps are **not** behind login. Anyone with the URL uses the dedicated app IAM
user. Mitigations: least-privilege IAM (#145), do not publish the URL, rotate
keys if leaked. Upgrade to auth: [#166](https://github.com/danolen/fantasy-baseball-platform/issues/166).

---

## GitHub Actions OIDC trust (#150)

**Decision (2026-07): branch-scoped trust; Environments deferred.**

`sub` is exact `StringEquals` on
`repo:danolen/fantasy-baseball-platform:ref:refs/heads/master`. Forks and
feature branches cannot assume the roles. Environments follow-up:
[#168](https://github.com/danolen/fantasy-baseball-platform/issues/168).

---

## Branch protection on `master` (#153)

**Decision (2026-07): enable a branch ruleset with maintainer bypass.**

Configured in GitHub → **Settings → Rules → Rulesets** (ruleset name
approximately `Protect master`; enforce **Active**).

| Rule | Setting |
|------|---------|
| Target | Branch `master` |
| Restrict deletions | On |
| Block force pushes | On |
| Require a pull request before merging | On (required approvals: **0** — solo) |
| Require status checks to pass | On (`lint-and-parse`; optionally `secret-scan`) |
| Bypass list | Maintainer (`danolen`) — **Always allow** |

| Actor | Direct push to `master`? |
|-------|---------------------------|
| Maintainer (bypass) | Yes — for trivial / urgent edits |
| Cursor agents, Dependabot, others | No — must open a PR; CI should pass |

This turns the `AGENTS.md` “no direct push / no self-merge” policy into a
technical control for non-admin tokens, without forcing the maintainer
through a PR for every one-line fix.

**Habit:** still prefer PRs for IAM, workflows, deps, and anything that
should wait on CI. Use bypass only for truly small changes.

---

## Follow-ups

| Topic | Ticket |
|-------|--------|
| Remove draft `CreateTable` + `allow_dynamodb_create_table = false` | #147 (deferred until draft redeploy) |
| Enable Streamlit Cloud authentication | #166 |
| Adopt GitHub Environments for GHA OIDC | #168 |
