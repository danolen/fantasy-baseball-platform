# dbt — local development

This directory holds the dbt project. Use local dbt for *fast feedback* (parse / compile / DAG inspection) on feature branches.

**dbt Cloud Jobs** (scheduled production runs) are **not configured yet**; when you add them, list each one under [dbt Cloud jobs](#dbt-cloud-jobs) so the schedule and commands stay visible in git.

## dbt Cloud jobs

There are **no dbt Cloud jobs** in this project yet. When you create them ([cloud.getdbt.com](https://cloud.getdbt.com/) → your project → **Deploy** → **Jobs**), add a row here for **each** production job with: **name**, **schedule (cron, UTC)**, **dbt command / selectors**, **target Athena schema** (production Streamlit apps use `ATHENA_SCHEMA=dbt_main`; see [`apps/draft-tool/README.md`](../apps/draft-tool/README.md)), and **notifications** (Slack / email / webhooks are configured only in Cloud).

| Job name | Schedule (cron, UTC) | dbt command / selectors | Target schema | Notifications |
|----------|------------------------|---------------------------|---------------|---------------|
| *— none yet —* | | | | |

### Manual maintenance commands (not jobs until wired in Cloud)

These flows are described in [Manual data maintenance](../README.md#manual-data-maintenance) on the root `README.md`. Run them locally (with Athena credentials) or fold them into dbt Cloud jobs when you add automation:

| Cadence | Command |
|---------|---------|
| Weekly after NFBC waivers | `dbt seed --select faab_remaining` |
| On demand (FTN / unmatched players) | `dbt seed && dbt build` |

### Changing a job

1. In dbt Cloud, open **Deploy** → **Jobs** → select the job → edit **Schedule**, **Commands**, **Environment**, or **Notifications** → **Save**.
2. Optionally run **Run now** and confirm Athena tables and downstream Streamlit apps look correct.
3. Update the **Production jobs** table above so **cron, commands, target schema, and notifications** match what is saved in Cloud.
4. If schema or model contracts change, update app secrets or docs in the same PR when possible.

## Model tags

Tags are set in [`dbt_project.yml`](dbt_project.yml) on `main/` marts only (upstream `source` / `stage` models stay untagged and are pulled in through the DAG).

Use a **leading `+`** on the selector so every upstream ref builds:

| Tag | Purpose | Typical command |
|-----|---------|-------------------|
| `inseason` | In-season Streamlit app — `mart_faab_worksheet`, `mart_faab_unmatched`, `mart_weekly_lineup_inputs`, and `mart_weekly_projections` (weekly projections feed the other marts). | `dbt build --select +tag:inseason` |
| `preseason` | Draft tool — preseason overall ranking marts (ME / OC / 50s) and `mart_sgp_percentiles`. | `dbt build --select +tag:preseason` |
| `on_demand` | Rest-of-season overall ranking marts (heavier path; not required for the draft UI today). | `dbt build --select +tag:on_demand` |

Each tagged mart has **one** of these tags. Shared upstream (for example `mart_sgp_factors`) stays **untagged** so it is built whenever a path that depends on it runs.

Inspect a slice: `dbt ls --select +tag:inseason`.

## One-time setup

```bash
# From the repo root. Requires Python >= 3.10 per dbt-athena 1.10+.
python3 -m venv venv         # if you don't already have one
source venv/bin/activate
pip install -r requirements-dev.txt

cd dbt
dbt deps                      # installs dbt-labs/dbt_utils per packages.yml
```

## Everyday commands

Run these from the `dbt/` directory. The repo-local [`profiles.yml`](profiles.yml)
is picked up automatically — no `DBT_PROFILES_DIR` needed.

```bash
dbt parse                     # validate Jinja, refs, sources, macros (no AWS)
dbt compile                   # render model SQL to target/ (no AWS)
dbt ls --select <model>+      # show DAG selector
dbt compile --select <model>  # see the exact SQL a single model produces
```

None of the above touches Athena — placeholder AWS values in `profiles.yml`
are sufficient. If a command complains about credentials, it's trying to
connect; double-check the command you ran.

## Running builds locally (optional, not required)

If you ever want to run `dbt build` / `dbt run` against Athena from your laptop,
either:

1. **Source the repo `.env`:**
   ```bash
   set -a; source ../.env; set +a
   dbt build --select <model>
   ```
2. **Export env vars manually:**
   ```bash
   export ATHENA_S3_OUTPUT=s3://your-bucket/staging/
   export ATHENA_REGION=us-east-1
   export ATHENA_SCHEMA=dbt_main_dev
   export ATHENA_DATABASE=AwsDataCatalog
   dbt build --select <model>
   ```

For now the recommended workflow is:

1. Write / edit models locally
2. `dbt parse` + `dbt compile` to catch obvious errors
3. Push the feature branch
4. Validate before merge: `dbt parse` in CI, and—when you use Athena—`dbt build` locally or via dbt Cloud (ad-hoc or **Jobs**, once those exist)
5. Open a PR when you are satisfied builds and apps behave as expected

## Project layout

- `models/source/` — select from external tables, add partition fields, filter to current data
- `models/stage/` — intermediate transformations
- `models/main/` — consumption-ready marts for apps
- `seeds/` — small static CSVs (league config, player overrides, etc.)
- `macros/` — project macros
- `snapshots/` — snapshot definitions
- `tests/` — singular tests
- `analyses/` — ad-hoc analyses (not built by `dbt run`)
