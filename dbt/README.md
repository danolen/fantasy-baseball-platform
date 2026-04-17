# dbt — local development

This directory holds the dbt project. **Production builds still run in dbt Cloud.**
Local dbt here is for *fast feedback* (parse / compile / DAG inspection) on feature
branches before pushing to dbt Cloud for a real build.

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
4. Run `dbt build` in **dbt Cloud** against the feature branch
5. Open a PR when the Cloud build passes

## Project layout

- `models/source/` — select from external tables, add partition fields, filter to current data
- `models/stage/` — intermediate transformations
- `models/main/` — consumption-ready marts for apps
- `seeds/` — small static CSVs (league config, player overrides, etc.)
- `macros/` — project macros
- `snapshots/` — snapshot definitions
- `tests/` — singular tests
- `analyses/` — ad-hoc analyses (not built by `dbt run`)
