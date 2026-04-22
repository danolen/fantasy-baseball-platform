# Fantasy Baseball Platform

A personal data lakehouse and analytics platform for fantasy baseball draft preparation and in-season decision making. Built with dbt, Streamlit, and AWS.

---

## Repository Structure

| Directory | Description |
|-----------|-------------|
| [`dbt/`](dbt/) | dbt project -- Iceberg tables in Athena using a medallion architecture (source / stage / main). See [`dbt/README.md`](dbt/README.md) for local setup. |
| [`apps/draft-tool/`](apps/draft-tool/) | Streamlit draft tool -- player rankings, projected stats, real-time draft tracking via DynamoDB |
| [`apps/in-season-tool/`](apps/in-season-tool/) | Streamlit in-season tool -- FAAB worksheet, weekly lineup recommendations |
| [`utils/`](utils/) | Utility scripts for data operations (e.g., S3 uploads) |

Production dbt builds run in **dbt Cloud**. Local dbt (`dbt parse` / `dbt compile`)
is optional but speeds up feature-branch iteration; install with
`pip install -r requirements-dev.txt` and see [`dbt/README.md`](dbt/README.md).

---

## Architecture

### Storage
- **Amazon S3** with `year=YYYY/month=MM/day=DD/` partitioning

### Data Architecture
- Lakehouse on **Amazon Athena** with external tables over raw CSV/TSV files
- All source fields are strings; type casting and normalization happen in dbt
- Logical partitioning by ingestion date

### Transformation (`dbt/`)
- dbt creates **Iceberg** tables in Athena
- Medallion-style layers:
  - **Source** -- select from external tables, add partition fields, filter to current data
  - **Stage** -- intermediate transformations not exposed to downstream consumers
  - **Main** -- consumption-ready tables for BI tools and apps

### Draft Tool (`apps/draft-tool/`)
- **Streamlit** web app deployed to Streamlit Community Cloud
- Player rankings and valuations across contest formats
- Real-time filtering, sorting, and ADP charts
- Draft tracking persisted in **Amazon DynamoDB**
- Mobile- and desktop-friendly

### Access Control
- AWS IAM roles

---

## Goals & Motivation

- Build hands-on experience with **lakehouse architecture**
- Use **dbt** as the core transformation layer
- Design for **incremental growth** in data volume and complexity
- Create a **real, usable product**, not a toy dataset
- Practice making pragmatic trade-offs around cost, tooling, and scope

Although the current dataset is small, the architecture is designed to scale naturally as new data sources and products are added.

---

## Planned Enhancements

- **In-season tools** -- Streamlit app in `apps/in-season-tool/` for add/drop decisions, lineup optimization
- **Orchestration** -- Airflow (or similar) for ingestion, dbt builds, and app refreshes
- **dbt improvements** -- incremental materializations, tests, documentation, macros

---

## Manual data maintenance

A few seeds need periodic hand-updates while headless ingestion is still pending (Phase 2b):

| Seed | Update cadence | After updating |
|------|----------------|----------------|
| [`dbt/seeds/faab_remaining.csv`](dbt/seeds/faab_remaining.csv) | Weekly, after NFBC waivers run | `dbt seed --select faab_remaining` in dbt Cloud; no full rebuild needed |
| [`dbt/seeds/ftn_nfbc_player_overrides.csv`](dbt/seeds/ftn_nfbc_player_overrides.csv) | As-needed when the FAAB app surfaces unmatched FTN players | `dbt seed && dbt build` |

---

## Disclaimer

This project is for personal use and learning. All data sources are accessed via legitimate paid subscriptions where required and are not redistributed.
