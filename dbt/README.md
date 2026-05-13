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

## dbt Cloud jobs

Production runs are configured in **dbt Cloud** (account URL: [cloud.getdbt.com](https://cloud.getdbt.com/) → your project → **Deploy** → **Jobs**). This section is the **in-repo catalog**: keep it aligned with Cloud so anyone (or a cloud agent) can see **what runs in production and how often** without opening the UI for routine questions.

**At a glance:** Production targets Athena schema **`dbt_main`** (Streamlit production `ATHENA_SCHEMA`; see [`apps/draft-tool/README.md`](../apps/draft-tool/README.md)). dbt Cloud **Environment** variables for production jobs should use that schema unless you intentionally deploy elsewhere. Expect at least one **primary scheduled job** that builds the DAG (typically `dbt build` or the equivalent steps in Cloud). Separately, the repo documents a **weekly** `faab_remaining` seed refresh and an **on-demand** `dbt seed && dbt build` path for FTN / NFBC overrides — see [Manual data maintenance](../README.md#manual-data-maintenance).

### Production jobs

| Job name | Schedule (cron, UTC) | dbt command / selectors | Target schema | Notifications |
|----------|------------------------|---------------------------|---------------|---------------|
| **Primary production build** | *Paste from dbt Cloud → Job → Schedule (note UI timezone; store cron in UTC here).* | *Paste execution commands from Cloud (often a single `dbt build`, or `dbt build` plus selectors).* | `dbt_main` | *Paste from Job → Notifications (Slack, email, webhooks are configured in Cloud only).* |
| **FAAB remaining seed** | Weekly after NFBC waivers — *set cron in Cloud to match your waiver day/time.* | `dbt seed --select faab_remaining` | `dbt_main` | *Same as the job that runs this command (dedicated job or a step in a larger job).* |
| **FTN / NFBC player overrides** | On demand (when unmatched FTN players appear in the FAAB app). | `dbt seed && dbt build` (see [Manual data maintenance](../README.md#manual-data-maintenance); narrow commands in Cloud if you later split this into selectors). | `dbt_main` | *Per Job settings in Cloud.* |

The **FAAB** and **FTN overrides** rows mirror [Manual data maintenance](../README.md#manual-data-maintenance) in the root `README.md`. If you implement them as **steps inside one job** instead of separate jobs, keep one row per **logical** workload but note “step N of job *X*” in the *Job name* or *Commands* column.

### Changing a job

1. In dbt Cloud, open **Deploy** → **Jobs** → select the job → edit **Schedule**, **Commands**, **Environment**, or **Notifications** → **Save**.
2. Optionally run **Run now** and confirm Athena tables and downstream Streamlit apps look correct.
3. Update the table above so **cron, commands, target schema, and notifications** match what is saved in Cloud (especially after renames or selector changes).
4. If schema or model contracts change, update app secrets or docs in the same PR when possible.

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
