"""
Planning issue definitions for danolen/fantasy-baseball-platform.

Each entry below is exactly one GitHub issue body. The `create_planning_issues.py`
runner reads this list, creates the issues via the gh CLI, and cross-links epics
to their children once all numbers are known.

Issue key conventions
---------------------
- `ROADMAP`: top-level tracking issue (one per project).
- `A1`, `A2`, ...: Phase 1 (automation) epics.
- `B1`, `B2`, ...: Phase 2 (in-season app) epics.
- `C1`, `C2`, ...: Phase 3 (AI) epics.
- `D1`, `D2`: standalone cross-cutting issues (no epic).
- `A1.1`, `A1.2`, ...: leaf issues that roll up to the named epic.

To edit a ticket before creation, edit the relevant entry in this file. To add
or remove tickets, add or delete entries. Run the runner with `--dry-run` to
preview the create plan without touching GitHub.
"""

from __future__ import annotations

from textwrap import dedent

REPO = "danolen/fantasy-baseball-platform"


def _b(s: str) -> str:
    """Dedent and strip leading/trailing blank lines from a triple-quoted body."""
    return dedent(s).strip("\n")


ISSUES: list[dict] = []


def _add(**kw) -> None:
    ISSUES.append(kw)


# ---------------------------------------------------------------------------
# Roadmap (top-level tracking issue)
# ---------------------------------------------------------------------------

_add(
    key="ROADMAP",
    kind="roadmap",
    title="Roadmap: data platform + AI engineering",
    labels=["epic"],
    body_intro=_b("""
        Top-level tracking issue for the platform roadmap.

        **Phasing (locked in 2026-05-13 planning session)**

        1. **Phase 1 — Automation.** Eliminate as much manual data work as
           possible so the in-season tools always show fresh data without me
           typing commands. Prefect for anything auth-gated / multi-step;
           GitHub Actions only for single-call cron tasks.
        2. **Phase 2 — In-season app improvements.** Build the marts and UI
           that the *The Process* in-season management chapter argues are the
           highest-leverage tools: projected standings, category mobility,
           FAAB binning + budget pacing, pitcher streaming surface, IL stash
           watch, ROS rankings, trade evaluator.
        3. **Phase 3 — AI engineering.** Slow, learning-first. RAG over the
           book → structured-output news pipeline → FAAB co-pilot agent →
           end-of-season retrospective generator. Every ticket includes a
           short reading list and 1–2 hand-coding exercises before the
           implementation step.

        **Tool choices (defer to Washington Nationals job stack on ties)**

        - Python + SQL for transforms and apps.
        - Prefect on AWS Fargate/ECS for orchestration.
        - Athena (Trino-compatible) + Iceberg as the warehouse.
        - DuckDB for local validation and any future ingestion staging.
        - Terraform for new AWS resources.
        - GitHub Actions for CI and the simplest cron tasks.

        **Out of scope (for now)**

        - Underdog ADP (revisit in offseason).
        - Major rewrite of the lineup optimizer; current greedy version is
          good enough. Captured as a nice-to-have.

        **Epics**
        (auto-populated once children are created)
    """),
)

# ---------------------------------------------------------------------------
# Phase 1 — Automation
# ---------------------------------------------------------------------------

# Epic A1 -------------------------------------------------------------------
_add(
    key="A1",
    kind="epic",
    title="Epic: Repo hygiene & CI baseline",
    labels=["epic", "phase:1-automation", "area:platform"],
    body_intro=_b("""
        **Phase:** 1 (automation)
        **Area:** platform
        **Goal:** A working CI baseline (lint + `dbt parse` on every PR),
        clean PR/issue templates, and dependency pinning. No new features.

        **Why it matters**
        Every other Phase 1 ticket lands more safely once we have these
        guardrails. Today the repo has zero workflows, no templates, and
        unpinned dependencies — small footguns that compound as automation
        grows.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="A1.1",
    kind="leaf",
    parent_key="A1",
    title="CI: ruff + dbt parse on every PR",
    labels=["phase:1-automation", "area:platform", "chore", "quick-win"],
    body=_b("""
        **Outcome**
        A `.github/workflows/ci.yml` runs on every PR and on pushes to
        `master`. It runs:
        - `ruff check apps/ utils/ scripts/`
        - `dbt parse` from `dbt/` (no AWS creds required)

        **Why it matters**
        Cheap, immediate signal on broken Jinja, ref typos, and lint
        regressions. Pre-existing style issues are not blocking per
        `AGENTS.md`; this job should pass on master today.

        **Files / paths likely touched**
        - `.github/workflows/ci.yml` (new)
        - `pyproject.toml` or `ruff.toml` (optional, only if we want config)

        **Acceptance criteria**
        - [ ] PR that introduces a Jinja syntax error fails CI.
        - [ ] PR that introduces a serious ruff error fails CI.
        - [ ] CI runs in under 2 minutes.
        - [ ] No AWS credentials referenced in this workflow.
    """),
)

_add(
    key="A1.2",
    kind="leaf",
    parent_key="A1",
    title="Add PR template and issue templates",
    labels=["phase:1-automation", "area:platform", "chore"],
    body=_b("""
        **Outcome**
        - `.github/PULL_REQUEST_TEMPLATE.md` reminds the author of the
          AGENTS.md rules (feature branch, small PRs, tests for new dbt
          marts) and prompts for screenshots / a short test plan.
        - `.github/ISSUE_TEMPLATE/feature.md`, `bug.md`,
          `dbt-mart.md`, `automation-job.md` give Jira-like starting points.

        **Acceptance criteria**
        - [ ] New PRs auto-populate the template.
        - [ ] New issues offer the four templates.
    """),
)

_add(
    key="A1.3",
    kind="leaf",
    parent_key="A1",
    title="Remove or replace the dead DEPLOYMENT.md reference",
    labels=["phase:1-automation", "area:draft-tool", "chore", "quick-win"],
    body=_b("""
        **Outcome**
        Either delete the `See DEPLOYMENT.md` line from
        `apps/draft-tool/app.py` (since the file does not exist in this
        repo), or add a minimal `DEPLOYMENT.md` that lists the env vars
        and Streamlit Cloud setup. Default: delete and point to the
        existing `apps/draft-tool/README.md`.

        **Acceptance criteria**
        - [ ] No references in code to a non-existent file.
    """),
)

_add(
    key="A1.4",
    kind="leaf",
    parent_key="A1",
    title="Pin Python dependency versions",
    labels=["phase:1-automation", "area:platform", "chore", "quick-win"],
    body=_b("""
        **Outcome**
        `requirements.txt` and `requirements-dev.txt` pin every direct
        dependency to a concrete version (e.g. `streamlit==1.39.0`).
        Streamlit Community Cloud and Cursor Cloud Agents will then build
        reproducibly across the season.

        **Approach**
        - Activate the local venv from `setup.sh`.
        - Run `pip freeze` to capture the current working versions.
        - Replace the unpinned lines with the pinned versions, keeping
          comments and ordering intact.
        - Verify `streamlit run apps/in-season-tool/app.py` still imports
          cleanly.

        **Acceptance criteria**
        - [ ] No `>=`, `~=`, or fully-unpinned packages in either file.
        - [ ] Both apps still launch with the pinned versions.
    """),
)

# Epic A2 -------------------------------------------------------------------
_add(
    key="A2",
    kind="epic",
    title="Epic: Document & tag dbt orchestration",
    labels=["epic", "phase:1-automation", "area:dbt"],
    body_intro=_b("""
        **Phase:** 1 (automation)
        **Area:** dbt
        **Goal:** Make dbt Cloud's schedule and selector strategy
        legible from the repo, and add tags so future Prefect flows can
        trigger narrow slices instead of full builds.

        **Why it matters**
        Right now the orchestration is tribal knowledge in dbt Cloud's UI.
        When Phase 1 vendor flows land, each one needs to trigger only the
        relevant slice of the DAG.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="A2.1",
    kind="leaf",
    parent_key="A2",
    title="Document current dbt Cloud jobs in dbt/README.md",
    labels=["phase:1-automation", "area:dbt", "chore"],
    body=_b("""
        **Outcome**
        A `## dbt Cloud jobs` section in `dbt/README.md` lists every
        production dbt Cloud job with: name, schedule (cron), selectors,
        target schema, notifications.

        **Acceptance criteria**
        - [ ] Anyone reading the repo (or a future cloud agent) can
              answer "what runs in prod, when?" without opening dbt Cloud.
        - [ ] A short procedure for changing a job is included.
    """),
)

_add(
    key="A2.2",
    kind="leaf",
    parent_key="A2",
    title="Add tag-based selectors to dbt models",
    labels=["phase:1-automation", "area:dbt"],
    body=_b("""
        **Outcome**
        Models are tagged so we can run a slice rather than the entire
        DAG. Proposed tags:
        - `daily_inseason` — models the in-season app reads (
          `mart_weekly_lineup_inputs`, `mart_faab_worksheet`,
          `mart_faab_unmatched`).
        - `weekly_inseason` — anything refreshed on FAAB day only.
        - `preseason` — preseason ranking marts (kept off the weekly path).
        - `on_demand` — anything heavy that runs ad hoc.

        Tags are added in `dbt_project.yml` for whole folders where
        possible, and inline `config()` for specific exceptions.

        **Acceptance criteria**
        - [ ] `dbt build --select tag:daily_inseason` produces the same
              data the in-season tool needs.
        - [ ] `dbt build --select tag:preseason` produces the data the
              draft tool needs.
        - [ ] No model is in two conflicting tags by accident.
    """),
)

# Epic A3 -------------------------------------------------------------------
_add(
    key="A3",
    kind="epic",
    title="Epic: Quick-win ingestion via GitHub Actions",
    labels=["epic", "phase:1-automation", "area:automation"],
    body_intro=_b("""
        **Phase:** 1 (automation)
        **Area:** automation
        **Goal:** Automate the public, single-call vendor pulls before
        we stand up Prefect. These are simple cron jobs that don't need
        retries, dependencies, or state.

        **Why it matters**
        Drops 1–2 manual touches per week before the more complex Prefect
        flows ship.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="A3.1",
    kind="leaf",
    parent_key="A3",
    title="Daily NFBC ADP pull (GitHub Actions)",
    labels=["phase:1-automation", "area:automation", "quick-win"],
    body=_b("""
        **Outcome**
        A GitHub Actions workflow that runs daily, downloads the latest
        NFBC ADP file from the public endpoint, and uploads to
        `s3://dn-lakehouse-dev/nfbc/adp/year=YYYY/month=MM/day=DD/`
        using AWS creds from repo/environment secrets.

        Reuses `utils/upload_folder_to_s3.py`-style key layout.

        **Files / paths likely touched**
        - `.github/workflows/ingest-nfbc-adp.yml`
        - `utils/ingest/nfbc_adp.py` (small new script)

        **Acceptance criteria**
        - [ ] Workflow runs on a daily cron and on manual `workflow_dispatch`.
        - [ ] Idempotent: re-runs on the same day overwrite cleanly.
        - [ ] `dbt source freshness --select source:nfbc.adp` is fresh
              after a successful run (depends on A6.1 if not yet
              merged — otherwise verify the file landed).
    """),
)

_add(
    key="A3.2",
    kind="leaf",
    parent_key="A3",
    title="Weekly MPD player ID map refresh (GitHub Actions)",
    labels=["phase:1-automation", "area:automation", "quick-win"],
    body=_b("""
        **Outcome**
        A GitHub Actions workflow that runs weekly (Sunday 10:00 UTC),
        downloads the latest MPD player ID map, and overwrites
        `s3://dn-lakehouse-dev/mapping/mpd_player_id_map/SFBB Player ID Map - PLAYERIDMAP.csv`
        (no date partitions). AWS access via OIDC + Terraform
        (`terraform/github_actions_mpd_ingest/`).

        **Files / paths likely touched**
        - `.github/workflows/ingest-mpd-player-map.yml`
        - `utils/ingest/mpd_player_map.py`
        - `terraform/github_actions_mpd_ingest/`

        **Acceptance criteria**
        - [ ] Source `mapping.player_id_map` shows fresh data the next
              Sunday after merge.
    """),
)

# Epic A4 -------------------------------------------------------------------
_add(
    key="A4",
    kind="epic",
    title="Epic: Prefect-based vendor ingestion",
    labels=["epic", "phase:1-automation", "area:automation"],
    body_intro=_b("""
        **Phase:** 1 (automation)
        **Area:** automation
        **Goal:** Replace the manual download → S3 upload workflow for
        every auth-gated vendor with a Prefect flow that retries, logs,
        and notifies on failure.

        **Why it matters**
        These are the largest remaining manual touchpoints. Aligns the
        stack with the Washington Nationals job description (Prefect on
        AWS Fargate/ECS) which was the named tie-breaker for tool
        choices.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="A4.1",
    kind="leaf",
    parent_key="A4",
    title="ADR + minimal Prefect-on-AWS scaffolding",
    labels=["phase:1-automation", "area:automation", "area:platform"],
    body=_b("""
        **Outcome**
        An architectural decision record at `docs/adr/0001-prefect-on-aws.md`
        captures:
        - Prefect Cloud (free tier) vs. self-hosted Prefect server.
        - Worker pool runtime: AWS Fargate (push) vs. local dev.
        - Where vendor credentials live (AWS Secrets Manager).
        - How flows are packaged (Docker → ECR) and deployed.
        - Cost ceiling (this is a hobby project — keep it < $20/mo).

        Then ship the minimum scaffolding to run one flow end-to-end:
        - A `flows/` package layout with `pyproject.toml`.
        - A `Dockerfile` for the worker.
        - A `terraform/prefect/` module that creates: ECR repo, Fargate
          work pool, IAM task role with S3 + Secrets Manager access,
          minimal CloudWatch log group.
        - A "hello world" Prefect flow that writes a stamped file to
          `s3://dn-lakehouse-dev/_meta/prefect_hello/...`.

        **Why it matters**
        Every other A4.x ticket depends on this. Keep it minimal — do not
        pre-build for flows that aren't here yet.

        **Acceptance criteria**
        - [ ] ADR merged and accepted.
        - [ ] Hello-world flow runs on Fargate from a `prefect deploy`
              + `prefect deployment run` cycle.
        - [ ] Total monthly AWS cost increase is documented in the ADR
              and stays within the stated ceiling.

        **Notes**
        Prefer Prefect Cloud free tier unless the ADR finds a concrete
        reason to self-host. Free tier handles flow runs / scheduling
        and removes the operational burden of running a Prefect server.
    """),
)

_add(
    key="A4.2",
    kind="leaf",
    parent_key="A4",
    title="Prefect flow: NFBC standings + in-season players",
    labels=["phase:1-automation", "area:automation"],
    body=_b("""
        **Outcome**
        A Prefect flow `nfbc_in_season.py` that, on a daily schedule:
        1. Reads my NFBC session cookie from AWS Secrets Manager.
        2. Downloads the standings file and in-season players file for
           each of my leagues (`league_config.csv` seed lists them).
        3. Uploads to:
           - `s3://dn-lakehouse-dev/nfbc/standings/year=/month=/day=/`
           - `s3://dn-lakehouse-dev/nfbc/in-season-players/year=/month=/day=/`
        4. Triggers the dbt Cloud job for `tag:daily_inseason`
           (depends on A2.2 + A2.1).

        **Why it matters**
        Drops the most-painful weekly manual step (manual NFBC export +
        upload) and is the bottleneck for `mart_weekly_lineup_inputs`
        to be reliably fresh.

        **Acceptance criteria**
        - [ ] Cookie expiry is detected and surfaces a clear failure
              notification (vs. silently uploading an HTML login page).
        - [ ] At least one full week runs without manual intervention.
        - [ ] If a league fails, the others still succeed.
    """),
)

_add(
    key="A4.3",
    kind="leaf",
    parent_key="A4",
    title="Prefect flow: Fangraphs rest-of-season projections",
    labels=["phase:1-automation", "area:automation"],
    body=_b("""
        **Outcome**
        Daily Prefect flow that authenticates to FanGraphs, downloads
        rest-of-season hitting + pitching projections, and uploads to:
        - `s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/hitting/year=/month=/day=/`
        - `s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/pitching/year=/month=/day=/`

        **Acceptance criteria**
        - [ ] Source freshness clears daily for the two FG ROS sources.
        - [ ] Credentials live only in AWS Secrets Manager.
    """),
)

_add(
    key="A4.4",
    kind="leaf",
    parent_key="A4",
    title="Prefect flow: FTN FAAB + projections (Sunday weekly)",
    labels=["phase:1-automation", "area:automation"],
    body=_b("""
        **Outcome**
        Weekly Prefect flow that runs every Sunday morning (before NFBC
        FAAB processing), authenticates to FTN, downloads:
        - Vlad's FAAB recommendations CSV
        - Preseason and any updated in-season projections
        and uploads to the existing FTN source prefixes.

        Then triggers the dbt Cloud job for `tag:weekly_inseason` so
        `mart_faab_worksheet` reflects the latest recs before I sit
        down to bid.

        **Acceptance criteria**
        - [ ] Flow completes by 9am ET Sunday on a reliable cadence.
        - [ ] `mart_faab_worksheet` reflects the latest FTN bids in the
              app by 9:30am ET Sunday.
        - [ ] On failure, I'm notified via Prefect's UI or email — no
              silent failures going into FAAB.
    """),
)

_add(
    key="A4.5",
    kind="leaf",
    parent_key="A4",
    title="Prefect flow: Razzball weekly + weekend projections",
    labels=["phase:1-automation", "area:automation"],
    body=_b("""
        **Outcome**
        Weekly Prefect flow (Monday early AM for weekly; Thursday for
        weekend) that authenticates to Razzball and uploads:
        - Weekly hitting → `razzball/projections/weekly/hitting/`
        - Weekly pitching → `razzball/projections/weekly/pitching/`
        - Weekend hitting → `razzball/projections/weekly/weekend_hitting/`

        **Acceptance criteria**
        - [ ] Three sources clear `dbt source freshness` after each run.
        - [ ] Lineup-optimizer inputs are populated for the upcoming
              scoring period without manual touch.
    """),
)

# Epic A5 -------------------------------------------------------------------
_add(
    key="A5",
    kind="epic",
    title="Epic: Operator-maintained seeds → self-updating",
    labels=["epic", "phase:1-automation", "area:dbt", "area:in-season-tool"],
    body_intro=_b("""
        **Phase:** 1 (automation)
        **Area:** dbt + in-season-tool
        **Goal:** Stop hand-editing CSV seeds + opening PRs as a routine
        in-season chore.

        **Why it matters**
        Today `faab_remaining.csv` is updated weekly and
        `ftn_nfbc_player_overrides.csv` is updated as-needed. Both are
        small but they break my "look at the app and act" loop on
        Sunday mornings.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="A5.1",
    kind="leaf",
    parent_key="A5",
    title="Derive faab_remaining from NFBC standings file",
    labels=["phase:1-automation", "area:dbt", "quick-win"],
    body=_b("""
        **Outcome**
        Replace the manually maintained `dbt/seeds/faab_remaining.csv`
        with a staging model that reads "FAAB Remaining" from the NFBC
        standings export (already a source).
        `mart_faab_worksheet` joins to the new model instead of the seed.

        **Acceptance criteria**
        - [ ] `dbt/seeds/faab_remaining.csv` deleted.
        - [ ] `mart_faab_worksheet` shows the same FAAB remaining
              numbers it does today on the same date.
        - [ ] At least one historical snapshot in dbt source data
              confirms parity.
    """),
)

_add(
    key="A5.2",
    kind="leaf",
    parent_key="A5",
    title="In-app editor for FTN ↔ NFBC player overrides",
    labels=["phase:1-automation", "area:in-season-tool"],
    body=_b("""
        **Outcome**
        The "Unmatched FTN" expander in the in-season tool gains an
        inline editor: pick the correct NFBC player from a dropdown,
        click save, and the mapping persists. Backed by a new DynamoDB
        table `fantasy_baseball_ftn_overrides` (simplest path; mirrors
        the draft tool's DynamoDB pattern).

        The dbt source for the overrides switches from the seed file
        to the DynamoDB table.

        **Acceptance criteria**
        - [ ] Saving an override in the app surfaces in
              `mart_faab_worksheet` after the next dbt build.
        - [ ] `dbt/seeds/ftn_nfbc_player_overrides.csv` removed (or
              backed up to S3 and removed from `seeds/`).

        **Notes**
        Considered an in-app PR-opener via PyGitHub but rejected as
        over-engineered for the volume involved (a handful of overrides
        per season).
    """),
)

_add(
    key="A5.3",
    kind="leaf",
    parent_key="A5",
    title="Surface 'unmatched FTN' count in the in-season app header",
    labels=["phase:1-automation", "area:in-season-tool", "quick-win"],
    body=_b("""
        **Outcome**
        The in-season tool header shows a small badge:
        `🟡 N unmatched FTN players` when `mart_faab_unmatched` is
        non-empty, linking to the FAAB Worksheet expander. Disappears
        when zero.

        **Why it matters**
        Today the count is only visible by drilling into the expander —
        easy to miss before FAAB.
    """),
)

# Epic A6 -------------------------------------------------------------------
_add(
    key="A6",
    kind="epic",
    title="Epic: Data freshness + dbt test coverage",
    labels=["epic", "phase:1-automation", "area:dbt", "area:platform"],
    body_intro=_b("""
        **Phase:** 1 (automation)
        **Area:** dbt + platform
        **Goal:** When ingestion silently breaks, I know within hours.
        When mart logic regresses, CI catches it.

        **Why it matters**
        Today there are zero schema tests and zero source freshness
        configured. As Prefect flows take over, freshness becomes the
        canonical "did the pipeline work today?" signal.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="A6.1",
    kind="leaf",
    parent_key="A6",
    title="Add source freshness blocks to every dbt source",
    labels=["phase:1-automation", "area:dbt", "quick-win"],
    body=_b("""
        **Outcome**
        Every entry in `dbt/models/source/**/_sources.yml` gets a
        `freshness:` block with `warn_after` / `error_after` thresholds
        scaled to the vendor's cadence (e.g. NFBC standings: warn 24h,
        error 36h; FTN FAAB: warn 8 days; preseason FG: skip).

        `dbt source freshness` can then be run as a single command to
        identify stale pipelines.

        **Acceptance criteria**
        - [ ] Every source has either a freshness block or an explicit
              comment explaining why one isn't applicable.
        - [ ] `dbt source freshness` runs in CI nightly (separate
              workflow, requires AWS creds — see notes).

        **Notes**
        The CI freshness check needs read-only AWS credentials. Use a
        dedicated IAM user/role for this with `athena:GetTable*` +
        `glue:GetTable*` only.
    """),
)

_add(
    key="A6.2",
    kind="leaf",
    parent_key="A6",
    title="Add schema tests to every mart",
    labels=["phase:1-automation", "area:dbt"],
    body=_b("""
        **Outcome**
        Each of the 12 marts gets a `_schema.yml` with at least:
        - `unique` + `not_null` on the join key.
        - `accepted_values` on enum-like columns (e.g. `format`,
          `league_key`).
        - `relationships` to any seed it joins to.

        **Why it matters**
        Today there are zero schema tests. A bad upstream rename will
        silently produce empty marts.

        **Acceptance criteria**
        - [ ] `dbt test` passes against current production data.
        - [ ] At least one purposely-broken PR demonstrates the tests
              fail.
    """),
)

_add(
    key="A6.3",
    kind="leaf",
    parent_key="A6",
    title="Add dbt unit tests for SGP and FAAB worksheet logic",
    labels=["phase:1-automation", "area:dbt"],
    body=_b("""
        **Outcome**
        Unit tests using dbt's native `unit_tests:` block (or
        `dbt-unit-testing` package) for:
        - `mart_sgp_factors`: a synthetic standings input produces the
          expected SGP per-stat factor.
        - `mart_faab_worksheet`: synthetic inputs (rosters, FTN bids,
          NFBC standings) produce the expected per-player row with the
          correct `%_of_budget` calc.

        **Notes**
        Follow the `adding-dbt-unit-test` skill — keeps mocked inputs
        small and exercises only the math, not the joins.
    """),
)

# Epic A7 -------------------------------------------------------------------
_add(
    key="A7",
    kind="epic",
    title="Epic: Terraform import of current AWS footprint",
    labels=["epic", "phase:1-automation", "area:platform"],
    body_intro=_b("""
        **Phase:** 1 (automation)
        **Area:** platform
        **Goal:** Get the AWS pieces this project already relies on into
        Terraform via `terraform import`, so future changes are reviewable
        and reversible.

        **Why it matters**
        Aligns with the Nationals tech stack and avoids
        click-ops drift as Phase 1 adds more AWS resources (Prefect on
        Fargate, Secrets Manager entries, new IAM users).

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="A7.1",
    kind="leaf",
    parent_key="A7",
    title="Terraform: import S3, Glue, Athena WG, DynamoDB, IAM",
    labels=["phase:1-automation", "area:platform"],
    body=_b("""
        **Outcome**
        `terraform/aws/` contains modules for:
        - S3 bucket `dn-lakehouse-dev` with the existing prefix layout.
        - Glue databases referenced by sources.
        - Athena workgroup `primary` (or whichever is in use).
        - DynamoDB draft state table prefix.
        - IAM users / roles consumed by the apps and dbt Cloud.

        All resources are `terraform import`ed first, so `plan` shows no
        changes. State is in S3 (or Terraform Cloud free tier).

        **Acceptance criteria**
        - [ ] `terraform plan` returns "no changes" against the live
              account.
        - [ ] README documents how to apply changes.

        **Notes**
        Keep `terraform/prefect/` (from A4.1) as a separate module to
        keep blast radius small.
    """),
)

# ---------------------------------------------------------------------------
# Phase 2 — In-season app improvements
# ---------------------------------------------------------------------------

# Epic B1 -------------------------------------------------------------------
_add(
    key="B1",
    kind="epic",
    title="Epic: Projected standings + category mobility",
    labels=["epic", "phase:2-app", "area:dbt", "area:in-season-tool", "book:in-season"],
    body_intro=_b("""
        **Phase:** 2 (in-season app)
        **Area:** dbt + in-season-tool
        **Goal:** Ship the single most-cited mid-/late-season tool from
        *The Process*: projected end-of-season standings plus the "+X up
        / -Y down per category" mobility view (pp. 191–193 of the 2024
        edition).

        **Why it matters**
        The book argues this is what shifts decision-making from
        theoretical value to "value to MY team." Drives every FAAB,
        lineup, and trade decision after May.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="B1.1",
    kind="leaf",
    parent_key="B1",
    title="dbt mart: mart_projected_standings_{format}",
    labels=["phase:2-app", "area:dbt", "book:in-season"],
    body=_b("""
        **Outcome**
        Three new marts, one per format (`oc`, `me`, `50s`):
        `mart_projected_standings_<format>`. For each league in
        `league_config.csv` of that format:
        - team rows with current category totals (from NFBC standings
          source)
        - plus the sum of rest-of-season projections for each team's
          rostered players (from `mart_rest_of_season_overall_rankings_*`)
        - producing projected end-of-season category totals.

        **Acceptance criteria**
        - [ ] One row per team per league.
        - [ ] Columns include: current_<stat>, ros_<stat>,
              projected_<stat>, projected_rank_<stat>, projected_points.
        - [ ] Spot-checked against a hand calculation for one league.
    """),
)

_add(
    key="B1.2",
    kind="leaf",
    parent_key="B1",
    title="dbt mart: mart_category_mobility",
    labels=["phase:2-app", "area:dbt", "book:in-season"],
    body=_b("""
        **Outcome**
        For each of my teams in each league, for each scoring category,
        compute:
        - `points_up_possible`: how many teams' current totals are still
          within reach.
        - `points_down_at_risk`: how many teams within striking distance
          below.
        - `closest_team_above` and `closest_team_below` for context.

        Mirrors the table on pages 191–193 of the 2024 edition.

        **Acceptance criteria**
        - [ ] Output is filterable by league_key and team_id.
        - [ ] Works for both my teams and opponents (useful for blocking
              moves late in the season).
        - [ ] Has dbt schema tests for unique (league_key, team_id, stat).
    """),
)

_add(
    key="B1.3",
    kind="leaf",
    parent_key="B1",
    title="In-season tool: new 'Standings' tab",
    labels=["phase:2-app", "area:in-season-tool", "book:in-season"],
    body=_b("""
        **Outcome**
        New tab in `apps/in-season-tool/app.py`:
        - League picker reuses sidebar.
        - Top: standings grid for the selected league with my row
          highlighted, ↑/↓ arrows on cells where my team can leapfrog or
          be passed (per `mart_category_mobility`).
        - Bottom: "Mobility summary" — Up / Down totals per category
          (the `Mobility Total` table from p. 192 of the book).

        **Acceptance criteria**
        - [ ] Renders for every league in `league_config.csv`.
        - [ ] Athena queries are cached with the existing 15-min TTL.
        - [ ] Empty-data fallback is graceful (similar to existing tabs).
    """),
)

# Epic B2 -------------------------------------------------------------------
_add(
    key="B2",
    kind="epic",
    title="Epic: Pitcher streaming surface (data + UI hints)",
    labels=["epic", "phase:2-app", "area:dbt", "area:in-season-tool", "book:lineup"],
    body_intro=_b("""
        **Phase:** 2 (in-season app)
        **Area:** dbt + in-season-tool
        **Goal:** Surface what *The Process* says about pitcher
        streaming — most weekly pitcher value is undrafted, two-start
        accuracy depends heavily on first-start day — without rewriting
        the lineup optimizer.

        **Why it matters**
        ~60% of top-75 weekly pitcher weeks go undrafted in 12-team
        leagues. Surfacing first-start day and a confidence hint lets me
        make better Sunday FAAB decisions in 30 seconds.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="B2.1",
    kind="leaf",
    parent_key="B2",
    title="Extend mart_weekly_lineup_inputs with SP/RP rows",
    labels=["phase:2-app", "area:dbt", "book:lineup"],
    body=_b("""
        **Outcome**
        Today `mart_weekly_lineup_inputs` is hitter-only. Add pitcher
        rows with:
        - `projected_starts` (0, 1, 2)
        - `first_start_day` (Mon..Sun, NULL for RP)
        - `is_two_start` boolean
        - `weekly_projection_value` from Razzball weekly pitching

        Resolves the "Phase 1c" TODO in
        `apps/in-season-tool/lineup_optimizer.py` (data side only).

        **Acceptance criteria**
        - [ ] Every rostered SP in any of my leagues appears as a row
              for the current week.
        - [ ] Two-start projections come from the Razzball weekly file.
        - [ ] dbt schema tests: `not_null` on `first_start_day` when
              `projected_starts > 0`.
    """),
)

_add(
    key="B2.2",
    kind="leaf",
    parent_key="B2",
    title="UI: two-start pitcher list with confidence hint",
    labels=["phase:2-app", "area:in-season-tool", "book:lineup"],
    body=_b("""
        **Outcome**
        New section in the Lineup Optimizer tab (or its own subtab):
        "Two-Start Pitchers". Lists rostered + free-agent two-start
        candidates sorted by `weekly_projection_value`, with a confidence
        column rendering:
        - 🟢 Mon-first (87% historical accuracy)
        - 🟡 Tue-first (68% historical accuracy)
        - 🔴 Sat/Sun-first (≤70%)

        Tooltip cites the source: *The Process* p. 217.

        **Acceptance criteria**
        - [ ] Renders for every league in the selector.
        - [ ] Free-agent two-start candidates are surfaced (joins to
              `mart_faab_worksheet`).
    """),
)

_add(
    key="B2.3",
    kind="leaf",
    parent_key="B2",
    title="Lineup optimizer v2 (MILP + Friday-lock + Utility Advantage)",
    labels=["phase:2-app", "area:in-season-tool", "book:lineup", "nice-to-have"],
    body=_b("""
        **Outcome (nice-to-have, low priority)**
        Replace the greedy `optimize_lineup` with a MILP via PuLP /
        scipy that:
        - Separates Monday-lock hitter slate from Friday-lock
          weekend slate.
        - Applies the "Utility Advantage" heuristic — prefers multi-pos /
          latest-start-time / DTD players for UTIL/MI/CI so a Friday
          swap is cheap.

        **Status**
        Current greedy solution is good enough; suboptimal lineups are
        easy to spot manually. Do not work on this unless it becomes a
        clear pain point, or as a learning exercise after Phase 1 is
        complete.

        **Acceptance criteria (when revisited)**
        - [ ] Same inputs as v1 produce a lineup at least as good.
        - [ ] Unit tests on a synthetic team with a forced Friday
              injury show the v2 result is cheaper to repair.
    """),
)

# Epic B3 -------------------------------------------------------------------
_add(
    key="B3",
    kind="epic",
    title="Epic: FAAB binning + budget pacing",
    labels=["epic", "phase:2-app", "area:dbt", "area:in-season-tool", "book:faab"],
    body_intro=_b("""
        **Phase:** 2 (in-season app)
        **Area:** dbt + in-season-tool
        **Goal:** Make the FAAB Worksheet think the way *The Process*
        argues we should: bid bucketing, theoretical bid, cross-format
        translation, and a budget pacing curve.

        **Why it matters**
        The book's strongest empirical FAAB findings — Triage / Tactical
        / Strategic budgeting, 2.0–2.4x bid ratio between 15-team and
        12-team formats, Elite-tier pacing curves from the 2025 appendix
        — are all currently invisible in the app.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="B3.1",
    kind="leaf",
    parent_key="B3",
    title="Add bid_bucket column to mart_faab_worksheet",
    labels=["phase:2-app", "area:dbt", "book:faab"],
    body=_b("""
        **Outcome**
        Each row gets `bid_bucket in ('triage','tactical','strategic')`.
        Derivation: rule-based using the FTN bid magnitude, position
        scarcity in the current week, and roster gaps. Documented inline
        in the SQL.

        **Acceptance criteria**
        - [ ] Bucket assignment is reproducible (unit-tested per A6.3).
        - [ ] Visible as a column in the FAAB Worksheet UI with emoji
              prefixes (🩹 / 🎯 / 🏆).
    """),
)

_add(
    key="B3.2",
    kind="leaf",
    parent_key="B3",
    title="Add theoretical_bid column to mart_faab_worksheet",
    labels=["phase:2-app", "area:dbt", "book:faab"],
    body=_b("""
        **Outcome**
        New column `theoretical_bid` computed via the formula on p. 199
        of the book:

        ```
        theoretical_bid = (player_projected_positive_weekly_values
                         / projected_remaining_undrafted_value_for_season)
                         * remaining_budget
        ```

        Inputs:
        - `player_projected_positive_weekly_values` from Razzball weekly
          ROS context.
        - `projected_remaining_undrafted_value_for_season` from a
          historical baseline (start: hardcoded constants per format,
          per book research; iterate later).
        - `remaining_budget` from A5.1.

        **Why it matters**
        Even when noisy, the column forces me to ask "is this player a
        truly special source of value, or a churn move?"

        **Acceptance criteria**
        - [ ] Theoretical bid renders as a column in the worksheet next
              to the FTN bid.
        - [ ] Tooltip explains the formula and cites the page.
    """),
)

_add(
    key="B3.3",
    kind="leaf",
    parent_key="B3",
    title="Cross-format bid translation (15-team ↔ 12-team)",
    labels=["phase:2-app", "area:dbt", "area:in-season-tool", "book:faab"],
    body=_b("""
        **Outcome**
        Replace the manual heuristic expander
        ("Cross-league-size FTN recs (manual)") in the in-season app
        with a derived column on `mart_faab_worksheet`:
        `bid_translated_to_other_format` using the empirical 2.0–2.4x
        median ratio from the 2025 appendix research.

        **Acceptance criteria**
        - [ ] No more manual heuristic table in the UI.
        - [ ] Translation applies to both directions (15→12 and 12→15).
    """),
)

_add(
    key="B3.4",
    kind="leaf",
    parent_key="B3",
    title="In-season tool: budget pacing widget",
    labels=["phase:2-app", "area:in-season-tool", "book:faab"],
    body=_b("""
        **Outcome**
        A small chart in the FAAB Worksheet tab showing for the selected
        league:
        - my cumulative FAAB spent by week (line)
        - Elite-tier vs. 1st-quartile cumulative spend curves from the
          2025 appendix research (shaded reference bands)
        - 7-day forward burn-rate indicator (am I overspending or
          hoarding?)

        **Acceptance criteria**
        - [ ] Renders for every league with a non-null FAAB budget in
              `league_config`.
        - [ ] Reference curves are values from the book; cite the page.
    """),
)

# Epic B4 -------------------------------------------------------------------
_add(
    key="B4",
    kind="epic",
    title="Epic: Stash + Super Two watch",
    labels=["epic", "phase:2-app", "area:dbt", "area:in-season-tool", "book:faab"],
    body_intro=_b("""
        **Phase:** 2 (in-season app)
        **Area:** dbt + in-season-tool
        **Goal:** Make IL stashes and prospect call-up timing first-class
        in the FAAB workflow, not something I track in a separate
        spreadsheet.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="B4.1",
    kind="leaf",
    parent_key="B4",
    title="dbt mart: mart_il_stashes",
    labels=["phase:2-app", "area:dbt", "book:faab"],
    body=_b("""
        **Outcome**
        Mart joining FTN FAAB recs to MLB IL status (source: NFBC
        in-season players includes injury flag), with:
        - estimated weeks_to_return (where available)
        - average historical FAAB stash prices by weeks-out (informed by
          the 2025 appendix research)

        **Acceptance criteria**
        - [ ] One row per IL'd player with a current FTN bid.
        - [ ] Has a `value_score` column rolling up urgency vs. expected
              cost.
    """),
)

_add(
    key="B4.2",
    kind="leaf",
    parent_key="B4",
    title="In-season tool: Super Two countdown + prospect watchlist widget",
    labels=["phase:2-app", "area:in-season-tool", "book:faab"],
    body=_b("""
        **Outcome**
        A small widget that shows:
        - Days until likely Super Two cutoff (range: 4/25 earliest,
          5/12 average, 5/26 latest).
        - A short list of top-50 hitter prospects who haven't been
          called up yet, with their team's MLB context.

        **Acceptance criteria**
        - [ ] Widget hidden after July 1.
        - [ ] Prospect list is derived from an existing data source if
              possible (Fangraphs prospect rankings if available; else
              a small static seed updated yearly).
    """),
)

# Epic B5 -------------------------------------------------------------------
_add(
    key="B5",
    kind="epic",
    title="Epic: Activate ROS marts + trade evaluator",
    labels=["epic", "phase:2-app", "area:in-season-tool"],
    body_intro=_b("""
        **Phase:** 2 (in-season app)
        **Area:** in-season-tool
        **Goal:** Use the 3 rest-of-season ranking marts that already
        exist in dbt but are not consumed by any app, and add a small
        trade evaluator on top of them.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="B5.1",
    kind="leaf",
    parent_key="B5",
    title="In-season tool: ROS rankings tab",
    labels=["phase:2-app", "area:in-season-tool", "quick-win"],
    body=_b("""
        **Outcome**
        A new tab "ROS Rankings" in the in-season tool that reads
        `mart_rest_of_season_overall_rankings_{oc,me,50s}` based on the
        selected league. Same filter / sort affordances as the draft
        tool's preseason rankings view.

        **Why it matters**
        These marts are already built but unused. Pure quick-win.
    """),
)

_add(
    key="B5.2",
    kind="leaf",
    parent_key="B5",
    title="In-season tool: trade evaluator widget",
    labels=["phase:2-app", "area:in-season-tool", "book:in-season"],
    body=_b("""
        **Outcome**
        A "Trade Evaluator" subtab where I paste two lists of player IDs
        ("I give" / "I get") and the app shows:
        - Total ROS $ delta.
        - Per-category ROS delta multiplied against my current
          mobility (depends on B1.2).
        - A one-line takeaway: "Improves your weakest category, neutral
          elsewhere — leans accept."

        **Acceptance criteria**
        - [ ] Empty / unmatched player IDs render a clean warning.
        - [ ] Mobility weighting can be toggled off for a pure
              theoretical-value view.
    """),
)

# ---------------------------------------------------------------------------
# Phase 3 — AI engineering (with explicit learning notes)
# ---------------------------------------------------------------------------

# Epic C1 -------------------------------------------------------------------
_add(
    key="C1",
    kind="epic",
    title="Epic: RAG over The Process",
    labels=["epic", "phase:3-ai", "area:ai"],
    body_intro=_b("""
        **Phase:** 3 (AI engineering)
        **Area:** ai
        **Goal:** Build a small RAG system over the 3 PDFs in
        `s3://dn-lakehouse-dev/context/TheProcessBook/` so I can ask the
        book questions from a CLI and from the in-season tool.

        **Why this is the right starting point**
        - The data is already in S3, fully owned, and immutable.
        - The 2024 full edition's text extraction is clean (no OCR
          needed for the main book).
        - It hits every fundamental: chunking, embeddings, vector
          search, prompt engineering, eval. Once C1 is done the patterns
          carry into C2 and C3.

        **Learning frame**
        Each child issue has a `Learning notes` section with a short
        reading list and 1–2 hand-coding exercises before any
        implementation. The intent is to internalize the concepts, not
        to ship code as fast as possible.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="C1.1",
    kind="leaf",
    parent_key="C1",
    title="ADR: pick the RAG stack",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        `docs/adr/0002-rag-stack.md` makes the following choices, with
        ~1 paragraph of reasoning each:
        - Orchestration framework: LangChain vs. LlamaIndex vs.
          rolling your own.
        - Embedding model: OpenAI `text-embedding-3-small` vs. a local
          model (e.g. BGE).
        - Vector store: DuckDB + `vss` extension, sqlite-vec, pgvector,
          or a managed service.
        - LLM provider: OpenAI gpt-4o-mini as default; budget ceiling.
        - Where the index lives (S3 vs. local file vs. managed).

        **Acceptance criteria**
        - [ ] One-page ADR merged.
        - [ ] Stated cost ceiling (hobby budget).
        - [ ] An explicit "what would change this decision" trigger
              section.

        **Learning notes**
        Reading list (pick 2):
        - *The illustrated RAG* (Jay Alammar blog post).
        - *What is RAG?* (Pinecone learning center).
        - LangChain "RAG From Scratch" notebooks (just the README).

        Exercises before writing the ADR:
        1. Hand-compute cosine similarity between three short
           sentences using a tokenizer of your choice. No code yet —
           just on paper or in a REPL. Predict which two are closest.
        2. In a Python REPL, embed those same sentences with
           `text-embedding-3-small` and confirm or refute your
           prediction.
    """),
)

_add(
    key="C1.2",
    kind="leaf",
    parent_key="C1",
    title="Build the indexing pipeline as a CLI",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        A CLI: `python -m ai.index_book` that:
        1. Downloads the 3 PDFs from S3.
        2. Extracts text per-page (existing `pypdf` works; OCR not
           required for the 2024 edition).
        3. Chunks into ~500-token segments with metadata `{file, page,
           chapter_heading}` (use the chapter headings we already
           identified in the planning conversation).
        4. Embeds and stores into the vector store from C1.1.
        5. Prints a summary table: total chunks, tokens, index size.

        **Acceptance criteria**
        - [ ] Re-runnable / idempotent (skips existing chunks by hash).
        - [ ] Chunk metadata always carries `page` so retrievals can
              cite back to a page number.

        **Learning notes**
        Reading list:
        - LlamaIndex docs section "Chunking strategies".
        - Pinecone's "Chunking Strategies for LLM Applications" article.

        Exercises before implementation:
        1. Hand-chunk one page of the book three ways: paragraph,
           sentence, fixed-token. Predict which retrieves better for the
           query "What does the book say about closer turnover?".
        2. Compute embedding costs at three chunk sizes for the full
           book; pick a chunk size that fits the cost ceiling.
    """),
)

_add(
    key="C1.3",
    kind="leaf",
    parent_key="C1",
    title="Build an 'Ask the Book' CLI with citations",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        CLI `python -m ai.ask_book "How should I think about FAAB stashes
        more than a month out?"` returns:
        - A concise answer.
        - 3–5 inline citations of `(file, page)` from the retrieved
          chunks.
        - The full retrieved chunks visible with `--verbose`.

        **Acceptance criteria**
        - [ ] Hallucinations are flagged: if the LLM returns a claim
              without a matching retrieved chunk, it's labeled
              "unsupported".
        - [ ] A small eval set of 10 hand-curated questions + expected
              page references lives at `ai/evals/ask_book.jsonl`.
        - [ ] An `eval` subcommand reports precision/recall on that set.

        **Learning notes**
        Reading list:
        - Anthropic's "Building Effective Agents" guide.
        - "RAG evaluation" sections of LangChain or LlamaIndex docs.

        Exercises before implementation:
        1. Write 10 questions you actually want to ask the book, paired
           with the page numbers where you think the answer lives.
        2. For 3 of those, hand-write the ideal answer. This becomes
           your eval gold standard.
    """),
)

_add(
    key="C1.4",
    kind="leaf",
    parent_key="C1",
    title="In-season tool: 'Ask the book' integration",
    labels=["phase:3-ai", "area:ai", "area:in-season-tool"],
    body=_b("""
        **Outcome**
        A small expander at the bottom of every tab in the in-season
        tool: "Ask The Process". Free-text input, calls the retriever +
        LLM behind a simple FastAPI or Streamlit-backend function.

        **Acceptance criteria**
        - [ ] Latency under 5s for typical questions.
        - [ ] Cost per query logged.
        - [ ] Cited pages render as inline links to nothing (just text;
              S3 PDFs aren't publicly served and we don't want them to
              be).

        **Learning notes**
        Reading list:
        - Streamlit's `st.cache_data` and `st.cache_resource` patterns
          (already used elsewhere in the app).

        Exercises:
        1. Decide which questions you'd ask the book once per session
           vs. once ever. That maps to `cache_data` TTL choice.
    """),
)

# Epic C2 -------------------------------------------------------------------
_add(
    key="C2",
    kind="epic",
    title="Epic: News pipeline with structured outputs",
    labels=["epic", "phase:3-ai", "area:ai", "area:automation"],
    body_intro=_b("""
        **Phase:** 3 (AI engineering)
        **Area:** ai + automation
        **Goal:** Daily-ish news ingestion of MLB injury / lineup /
        bullpen news from public RSS, run through an LLM that emits
        strict JSON tagged to my rostered players.

        **Why this is next**
        Builds on RAG fundamentals (C1) and adds: structured outputs,
        scheduled LLM jobs, joining LLM output to relational data, and
        evaluation in a more adversarial setting.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="C2.1",
    kind="leaf",
    parent_key="C2",
    title="Daily RSS scrape of MLB news sources to S3",
    labels=["phase:3-ai", "area:automation"],
    body=_b("""
        **Outcome**
        A simple cron (Prefect flow or GitHub Actions, whichever is
        simpler at the time) pulls public RSS from MLBTradeRumors and
        the FanGraphs RotoGraphs feed (and any other public source
        chosen in the issue's discussion) and writes raw entries to
        `s3://dn-lakehouse-dev/news/raw/year=/month=/day=/source=/`.

        **Acceptance criteria**
        - [ ] At least 3 days of clean data in S3.
        - [ ] No vendor TOS violations (RSS only).

        **Learning notes**
        This issue is mostly engineering, not AI. It exists so C2.2 has
        clean data to chew on.
    """),
)

_add(
    key="C2.2",
    kind="leaf",
    parent_key="C2",
    title="LLM step: structured-output extraction from news",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        For each scraped article, run an LLM with strict JSON schema:
        ```
        {
          "player_name": str,
          "event_type": "injury|return|callup|demotion|bullpen|trade|other",
          "severity": "minor|moderate|major|na",
          "days_out_estimate": int | null,
          "lineup_impact": "starting|bench|platoon|na",
          "raw_quote": str
        }
        ```
        Lands in `s3://dn-lakehouse-dev/news/structured/year=/month=/day=/`.

        **Acceptance criteria**
        - [ ] Schema is enforced (OpenAI structured outputs / Pydantic
              validation).
        - [ ] Articles producing zero player events return an empty
              array, not garbage.

        **Learning notes**
        Reading list:
        - OpenAI structured outputs guide.
        - Instructor library README (alternative pattern).

        Exercises:
        1. Hand-label 15 articles for the same schema. This is your eval
           gold standard.
        2. Predict where the LLM will fail before running it; track how
           accurate your predictions were.
    """),
)

_add(
    key="C2.3",
    kind="leaf",
    parent_key="C2",
    title="dbt mart + in-app filter for player news signals",
    labels=["phase:3-ai", "area:dbt", "area:in-season-tool"],
    body=_b("""
        **Outcome**
        - dbt source on the structured-news prefix.
        - Mart `mart_player_news_signals` joined to NFBC player IDs.
        - In the in-season tool, a "News for my players" subtab.

        **Acceptance criteria**
        - [ ] Player ID matching uses the MPD ID map and produces an
              "unmatched news" report (analogous to FTN unmatched).
    """),
)

_add(
    key="C2.4",
    kind="leaf",
    parent_key="C2",
    title="Eval harness for the news extraction step",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        - 50 hand-labeled articles at `ai/evals/news_extraction.jsonl`.
        - `python -m ai.evaluate_news_extraction` reports precision /
          recall per event_type and overall.
        - A simple regression check: a PR that changes the prompt and
          drops F1 by > X% requires explicit override.

        **Learning notes**
        Reading list:
        - "How to evaluate an LLM" guides (Hamel Husain's blog).

        Exercises:
        1. Disagree with the LLM on at least 3 of your 50 labels and
           hand-relabel them with a written rationale. Calibrates how
           messy this work really is.
    """),
)

# Epic C3 -------------------------------------------------------------------
_add(
    key="C3",
    kind="epic",
    title="Epic: FAAB co-pilot agent",
    labels=["epic", "phase:3-ai", "area:ai", "book:faab"],
    body_intro=_b("""
        **Phase:** 3 (AI engineering)
        **Area:** ai
        **Goal:** A small agent that, on Sunday morning, suggests bid
        ranges for my top FAAB targets, citing standings mobility, FTN
        recommendations, the news signals from C2, and *The Process*
        framework via the C1 retriever.

        **Why this is third**
        Requires everything that comes before it: structured news,
        retrieval over the book, mobility marts. Also introduces the
        agent / tool-use paradigm, which is qualitatively different
        from C1/C2.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="C3.1",
    kind="leaf",
    parent_key="C3",
    title="Design the agent's tool surface",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        Document at `docs/adr/0003-faab-copilot-tools.md` defining:
        - `query_athena(sql: str)` — read-only against `dbt_main` schema.
        - `get_mobility(league_key, team_id)` — wraps B1.2.
        - `get_ftn_bid(player_name, format)` — wraps mart_faab_worksheet.
        - `ask_the_book(query)` — wraps the C1 retriever.
        - `get_news_signals(player_name, days)` — wraps C2.3.

        Each tool has: typed input/output, side-effect classification
        (read-only here), error handling.

        **Acceptance criteria**
        - [ ] Schemas are JSON-serializable; clean prompt examples
              included.

        **Learning notes**
        Reading list:
        - Anthropic "Tool use" guide.
        - OpenAI function calling docs.

        Exercises:
        1. For each tool, write 2 example inputs the LLM might generate
           and the expected output. This is your tool eval.
    """),
)

_add(
    key="C3.2",
    kind="leaf",
    parent_key="C3",
    title="Implement the agent loop",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        `python -m ai.faab_copilot --league <key>` prints, for the top
        FAAB candidates this week:
        - Player name, position, FTN bid, theoretical bid.
        - 1–2 sentence narrative citing standings mobility + news +
          book.
        - Suggested bid range with high/low rationale.

        **Acceptance criteria**
        - [ ] Loop has a hard step cap to prevent runaways.
        - [ ] All tool calls and reasoning are logged to a JSONL trace.
        - [ ] Cost per run logged and bounded.

        **Learning notes**
        Reading list:
        - "How to build an agent" by Anthropic.
        - At least one negative case study (e.g. "Why agents fail" or
          "ReAct considered harmful" type post).

        Exercises:
        1. Write down 3 concrete failure modes you expect this agent
           to have. Implement at least one guardrail per failure mode
           in the loop before you turn it on for a real FAAB week.
    """),
)

_add(
    key="C3.3",
    kind="leaf",
    parent_key="C3",
    title="A/B retrospective: my bids vs. the co-pilot's suggestions",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        Track for one month (4 FAAB cycles):
        - My actual bid.
        - The co-pilot's suggested range.
        - The winning bid in each league.
        - Net "would I have done better following the co-pilot?".

        Write a short retrospective issue / blog post.

        **Acceptance criteria**
        - [ ] Data captured in a small CSV or Iceberg table.
        - [ ] Retrospective answers: when did the co-pilot help, when
              did it hurt, and what should the next iteration look like.
    """),
)

# Epic C4 -------------------------------------------------------------------
_add(
    key="C4",
    kind="epic",
    title="Epic: End-of-season retrospective generator",
    labels=["epic", "phase:3-ai", "area:ai"],
    body_intro=_b("""
        **Phase:** 3 (AI engineering)
        **Area:** ai
        **Goal:** When a season ends, generate a Markdown post-mortem
        per league: final standings analysis, my best/worst FAAB moves,
        category-by-category retrospective, suggested adjustments for
        next year, citing book chapters where applicable.

        **Why last**
        Lowest-stakes, high-value-once-a-year. Lets us exercise C1+C3
        patterns on a different problem shape.

        **Children**
        (auto-populated once children are created)
    """),
)

_add(
    key="C4.1",
    kind="leaf",
    parent_key="C4",
    title="Implement the retrospective generator",
    labels=["phase:3-ai", "area:ai"],
    body=_b("""
        **Outcome**
        `python -m ai.season_retrospective --league <key>` produces a
        Markdown file at `analyses/retrospectives/YYYY-<league>.md`
        with sections:
        - Final standings + how my team got there.
        - FAAB summary: best 3 adds, worst 3 adds, total spend pacing
          vs. the book's pacing curves.
        - Categorical regrets ("where did I leave points on the table").
        - Suggested adjustments grounded in book quotes.

        **Acceptance criteria**
        - [ ] Output is reviewable in a PR (Markdown only, no images
              required).
        - [ ] At least one human-spotted insight per league.

        **Learning notes**
        Reading list:
        - Any well-structured "annual retrospective" template.

        Exercises:
        1. Write your own retrospective for last season by hand (1–2
           pages) before generating one. This is the gold standard.
    """),
)

# ---------------------------------------------------------------------------
# Cross-cutting standalone issues
# ---------------------------------------------------------------------------

_add(
    key="D1",
    kind="leaf",
    parent_key=None,
    title="AGENTS.md: add 'Cursor Cloud specific instructions' for AI/RAG work",
    labels=["chore", "area:platform"],
    body=_b("""
        **Outcome**
        When the first C1 ticket lands, append a section to
        `AGENTS.md` covering:
        - Where the vector index lives.
        - Embedding model + version.
        - Eval set locations under `ai/evals/`.
        - Cost ceiling reminder.

        **Why it matters**
        Future cloud agents need this context to avoid blowing the
        budget or reindexing accidentally.
    """),
)

_add(
    key="D2",
    kind="leaf",
    parent_key=None,
    title="Maintain an ingestion automation matrix in AGENTS.md",
    labels=["chore", "area:platform"],
    body=_b("""
        **Outcome**
        Add a small table to `AGENTS.md` of every vendor and its
        current state: Manual / GitHub Actions / Prefect, with last-
        modified date. Update with every Phase 1 ingestion ticket
        closed.

        **Why it matters**
        Lets me see at a glance what's still manual without grepping
        the repo.
    """),
)


def get_issues() -> list[dict]:
    return ISSUES
