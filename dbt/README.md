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

## Source and seed tags

Tags live on **sources** (and a few **seeds** used only by the in-season FAAB path) so you can run **`dbt build --select tag:<name>+`**: each tagged root and **everything downstream** of it. That avoids selecting “all ancestors of a mart” (which would always pull e.g. `nfbc.standings` into in-season work even though that feed is really an off-season refresh for SGP).

**Typical commands**

| Slice | Command |
|-------|---------|
| In-season external data + downstream | `dbt build --select tag:inseason+` |
| Preseason / draft + downstream | `dbt build --select tag:preseason+` |

**Caveat:** `mart_weekly_projections` still **refs** preseason and ROS marts as columns. When dbt **builds** nodes selected by `tag:inseason+`, it will still run **parent** models those nodes depend on (so preseason / ROS marts may execute as prerequisites). The benefit of source tagging is which **external roots** you intentionally include in the slice selector—not that cross-slice refs disappear from the graph.

**Where tags are defined**

- Source tables: `models/source/*/_sources.yml` (and `_source.yml` for Razzball).
- In-season seeds: [`dbt_project.yml`](dbt_project.yml) under `seeds:` — `league_config`, `faab_remaining`, `ftn_nfbc_player_overrides`.

**Tag map (summary)**

| Tag | Meaning |
|-----|---------|
| `inseason` | External data that updates during the season: NFBC in-season players; Razzball weekly projections; Fangraphs **rest-of-season** projections and rosters (with preseason); FTN FAAB; plus the in-season seeds above. |
| `preseason` | Draft / preseason inputs (NFBC standings, players, ADP; Fangraphs/Razzball/FTN *preseason* projections; Fangraphs rosters; Underdog ADP). `nfbc.standings` stays here so SGP work does not ride the in-season selector. |
| *(multi)* | `mapping.player_id_map` is tagged `preseason` and `inseason` because both slices use the ID map. |

Inspect nodes: `dbt ls --select tag:inseason+`.

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

## Source freshness (#51)

Five live-ingest sources have `freshness:` blocks in `models/source/*/_sources.yml`
(scaled to Prefect / GHA cadence). Skipped sources have YAML comments explaining why.

```bash
dbt source freshness   # requires ATHENA_S3_OUTPUT + AWS creds
```

Nightly checks run in GitHub Actions (`.github/workflows/dbt-source-freshness.yml`).
IAM role setup: [`terraform/github_actions_dbt_freshness/README.md`](../terraform/github_actions_dbt_freshness/README.md).

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
