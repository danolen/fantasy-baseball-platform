# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is a Fantasy Baseball Analytics Platform with three main components:

| Component | Path | Purpose |
|-----------|------|---------|
| Draft Tool | `apps/draft-tool/` | Streamlit app — player rankings, ADP charts, draft tracking |
| In-Season Tool | `apps/in-season-tool/` | Streamlit app — FAAB worksheet, lineup optimizer |
| dbt Project | `dbt/` | Data transformations (Athena/Iceberg lakehouse) |

### Running services

- **Draft Tool**: `source venv/bin/activate && streamlit run apps/draft-tool/app.py --server.port 8501 --server.headless true`
- **In-Season Tool**: `source venv/bin/activate && streamlit run apps/in-season-tool/app.py --server.port 8502 --server.headless true`
- Both apps require `ATHENA_S3_OUTPUT` env var (or `.env` file) to connect to AWS. Without it, the app will render a config error page but still serve Streamlit UI.

### dbt commands (run from `dbt/` directory)

- `dbt parse` — offline validation of Jinja, refs, sources, macros (no AWS needed)
- `dbt compile` — renders SQL to `target/` (requires AWS credentials)
- `dbt deps` — installs dbt packages from `packages.yml`

See `dbt/README.md` for full details.

### Linting

No formal linter is configured in the repo. `ruff check apps/ utils/` can be used for Python linting (ruff is installed in the venv). Pre-existing style issues exist and are not blocking.

### Testing

- No Python test framework (pytest, unittest) is configured.
- For dbt: `dbt parse` validates model correctness offline. `dbt test` and `dbt build` require AWS credentials.
- Manual testing is done via the Streamlit apps.

### AWS dependency

All data flows through AWS (Athena, S3, DynamoDB). For full end-to-end testing, AWS credentials must be configured. The following environment variables are needed:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `ATHENA_S3_OUTPUT` (required, no default)

Without these, apps will start but show a config error. `dbt parse` works without credentials.

### Gotchas

- `requirements.txt` is at the repo root (not in `apps/`) because Streamlit Community Cloud expects it there.
- `requirements-dev.txt` adds `dbt-athena` for local dbt work; kept separate to avoid bloating Streamlit Cloud deploys.
- The `venv/` directory is at the repo root.
- `dbt compile` fails without AWS credentials — use `dbt parse` for offline validation.
- The in-season tool imports `lineup_optimizer` from its own directory (`apps/in-season-tool/lineup_optimizer.py`), so it must be run from that directory or the repo root with Streamlit's runner.
- Athena queries from the Streamlit apps take a few seconds on first load (cold cache). Streamlit caches results (TTL 15 min for rankings, 1 hour for percentiles) so subsequent loads are instant.
- The Draft Tool auto-loads player data on startup — no manual "load" step needed. It will show a spinner then a "Loaded X players" success message.
- `dbt compile` (with credentials) reports 92 models, 4 seeds, 18 sources. This is useful as a baseline when verifying model changes.
- `python3.12-venv` system package is required for creating the virtual environment on Ubuntu. The update script handles this.
