# AGENTS.md

## Maintainer rules and preferences

### Git (required)

- **Never commit directly to `master`.** All changes go on a **feature branch**, then a **pull request** for review. The maintainer merges PRs; agents should not merge to `master` from local or cloud sessions.
- Prefer branch names like `feat/<phase>-<short-description>` (e.g. `feat/1c-sp-streamer-v1`), `fix/...` for bug fixes, and `chore/...` for hygiene (tests, docs, config-only).

### How to ship work

- Prefer **small, incremental updates** (one focused PR per meaningful chunk) over large sweeping refactors or mega-diffs unless the maintainer explicitly asks for a bigger change set.
- Keep each change set **tied to a clear outcome** (e.g. one mart column set, one app tab slice) so review and rollback stay easy.

### AWS vs this repository

- **AWS** (S3, Glue, Athena, IAM, ECS, etc.) is handled by the maintainer unless they say otherwise. Agents work in **this repo** (dbt, Streamlit, seeds, Python utils, docs) and can describe needed AWS steps for the maintainer to apply.

---

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

### Token efficiency (please read first)

Cloud agent runs cost real money. Before doing anything else on a ticket, follow the rules below to avoid burning credits on work that was already done in the 2026-05-13 planning session.

**Pre-loaded context — do not redo this work**

- The planning roadmap lives in GitHub issues #35–#99 in this repo. Each ticket already contains the outcome, why-it-matters, files likely touched, acceptance criteria, and (for Phase 3 AI tickets) a reading list and pre-implementation exercises. **Read the ticket body in full before touching any code or exploring the repo.** The acceptance criteria are the scope; do not expand.
- A full repo audit was done on 2026-05-13. The state-of-the-platform facts you would otherwise rediscover are already encoded in the relevant issue bodies. If you need a quick map: 92 dbt models, 4 seeds, 18 sources, 12 marts, two Streamlit apps (`apps/draft-tool`, `apps/in-season-tool`), no CI yet, no `.github/` workflows, no Prefect/Airflow code, one operator helper at `utils/upload_folder_to_s3.py`.
- Phase 2 / 3 tickets cite specific pages of *The Process* (PDFs in `s3://dn-lakehouse-dev/context/TheProcessBook/`). **Do not re-extract the full book.** If you need a quote, fetch only the specific pages you need using `boto3` + `pypdf`. The `aws` CLI is not installed in this VM by default; use `boto3`.

**Tool-use rules that save tokens**

- Trust issue bodies as the spec. Don't pre-emptively re-derive context that the ticket already states.
- Use `Grep` and `Glob` before `Read`. Never speculatively `Read` an entire file unless the ticket says you need it.
- Batch independent tool calls in parallel within a single message instead of serializing them.
- Combine related shell commands into one `Shell` call with `&&` chains rather than multiple invocations.
- Avoid `computerUse`, `videoReview`, and `RecordScreen` unless the ticket is a visible UI change. Most Phase 1 work has no UI to record.
- Avoid spinning up sub-`Task` agents unless the work is genuinely broad exploration. Each subagent multiplies token cost.
- Prefer `dbt parse` (free, offline) over `dbt compile` / `dbt build` (slow, costs AWS). Only run AWS-touching commands when the acceptance criteria require it.
- Skip the testing walkthrough for dotfile / template / CI-yaml-only changes. Apply the system prompt's "/no-test for trivial changes" spirit — if the change is mechanical and obviously correct, ship it.

**Model selection: Composer vs. frontier**

Cursor's faster/cheaper model (Composer) is sufficient for most Phase 1 tickets. Phase 2 and Phase 3 work generally needs a frontier model. Rough guide — override if a specific ticket disagrees:

| Composer-friendly | Frontier model recommended |
| --- | --- |
| **A1.\*** repo hygiene (`#35`–`#38`): boilerplate CI YAML, PR/issue templates, dead-link removal, dependency pinning | **A4.1** Prefect ADR (`#43`): architectural decisions, cost analysis, infra scaffolding |
| **A2.\*** dbt docs/tags (`#39`, `#40`): README transcription, mechanical tag annotation | **A4.2–A4.5** Prefect vendor flows (`#44`–`#47`): auth-gated scrapes, retry/error design, integration |
| **A3.\*** GitHub Actions quick wins (`#41`, `#42`): standard cron workflow + small Python uploader | **A5.2** DynamoDB-backed override editor (`#49`): cross-cutting app + dbt + data-model change |
| **A5.1, A5.3** seed-source swap and unmatched-FTN badge (`#48`, `#50`) | **A6.3** dbt unit tests for SGP / FAAB worksheet (`#53`): requires understanding of book-derived logic |
| **A6.1, A6.2** dbt source freshness + schema tests (`#51`, `#52`): mechanical YAML | **A7.1** Terraform import (`#54`): high-risk infra, blast radius |
| **B5.1** ROS rankings tab (`#67`): mirrors existing draft-tool patterns | **B1.\*** Projected standings + mobility (`#55`–`#57`): novel math, multi-source joins |
| **D1, D2** AGENTS.md updates (`#81`, `#82`) | **B2.1, B3.\*, B4.\*, B5.2**: book-derived marts and widgets that need careful interpretation |
| | **All of C1–C4** (`#69`–`#80`): Phase 3 AI work is learning-first and benefits from frontier reasoning; the maintainer explicitly wants to *not* speed through these |

If a Composer-friendly ticket reveals unexpected complexity mid-task (ambiguity, integration with code you can't read in a single pass, novel algorithms), stop and ask the maintainer rather than guessing — that's still cheaper than a bad commit.

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
