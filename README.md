# Fantasy Baseball Platform

Personal lakehouse + apps for NFBC draft prep and in-season decisions. **dbt** on Athena/Iceberg, **Streamlit** apps, **Prefect** vendor ingest, AWS.

Roadmap lives in [GitHub issues](https://github.com/danolen/fantasy-baseball-platform/issues) (epics #83–#99 and Phase 2 app tickets). Agent notes: [`AGENTS.md`](AGENTS.md).

---

## What’s live

| Area | Highlights |
|------|------------|
| **In-season tool** | FAAB worksheet + what-if add/drop (split Mon–Thu / Fri–Sun); lineup optimizer (exact slot assignment, Monday lock / Friday hitter swap, Neutral `$` or Team-fit overall-pts weights); **Overall Standings** (rank/points, category mobility, Weekly Plan maintain/stretch vs team-fit Monday lineup); **ROS Rankings** (format marts, draft-tool filters) |
| **dbt marts** | FAAB worksheet; weekly lineup inputs; overall category mobility (+ field-edge / pts-per-unit); weekly category plan |
| **Ingest** | Prefect flows for NFBC in-season players + overall standings, FanGraphs ROS, FTN FAAB, Razzball weekly / Mon–Thu / weekend ([`flows/`](flows/)) |
| **Draft tool** | Rankings, ADP, DynamoDB draft tracking |
| **Platform** | CI (`ruff` + `dbt parse`), OIDC-friendly IAM sketches, security matrix in [`docs/security.md`](docs/security.md) |

---

## Repository layout

| Path | Role |
|------|------|
| [`dbt/`](dbt/) | Source → stage → main Iceberg models (Athena). See [`dbt/README.md`](dbt/README.md). |
| [`apps/draft-tool/`](apps/draft-tool/) | Streamlit draft app |
| [`apps/in-season-tool/`](apps/in-season-tool/) | Streamlit in-season app |
| [`flows/`](flows/) | Prefect vendor ingestion ([`flows/README.md`](flows/README.md), ADR [`docs/adr/0001-prefect-on-aws.md`](docs/adr/0001-prefect-on-aws.md)) |
| [`terraform/`](terraform/) | IAM / bootstrap modules (maintainer-applied) |
| [`docs/`](docs/) | Security, ADRs |
| [`utils/`](utils/) | Small operator helpers (e.g. S3 upload) |

---

## Architecture (short)

- **S3** lakehouse (`year=/month=/day=` partitions) → Athena external tables → dbt Iceberg (`dbt_source` / `dbt_stage` / `dbt_main`; seeds in profile schema `dbt`)
- Apps query Athena with cached loaders; draft tracking uses DynamoDB
- Secrets and IAM roles are per actor — see [`docs/security.md`](docs/security.md)

---

## Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt          # apps
pip install -r requirements-dev.txt      # + dbt-athena

# Apps (need ATHENA_S3_OUTPUT + AWS creds for data; UI still boots without)
streamlit run apps/draft-tool/app.py --server.port 8501
streamlit run apps/in-season-tool/app.py --server.port 8502

# dbt offline check
cd dbt && dbt parse

# Prefect dry-run (no AWS / Prefect API)
python flows/razzball_weekly.py --dry-run
```

---

## Manual seeds

| Seed | When |
|------|------|
| [`dbt/seeds/faab_remaining.csv`](dbt/seeds/faab_remaining.csv) | Weekly after NFBC waivers → `dbt seed --select faab_remaining` |
| [`dbt/seeds/ftn_nfbc_player_overrides.csv`](dbt/seeds/ftn_nfbc_player_overrides.csv) | When the FAAB unmatched badge needs a fix → `dbt seed` + rebuild FAAB models |

---

## Disclaimer

Personal use and learning. Paid data sources are not redistributed.
