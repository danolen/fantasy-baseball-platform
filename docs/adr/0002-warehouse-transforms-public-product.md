# ADR 0002 — Warehouse, transforms, and a public product

- **Status:** Proposed. Recommended decisions are in §3–§7. Merging this ADR accepts those recommendations. A fallback is named only where a second path is real.
- **Date:** 2026-09-30
- **Ticket:** #302
- **Depends on:** [ADR 0001](0001-prefect-on-aws.md) (Prefect Cloud Hobby stays)
- **Blocks:** any public UI. Planning can start after this ADR is accepted. The first public deploy waits until the IAM boundary in §6 exists.

This is a decision record. It does not migrate the warehouse, add Terraform, or rewrite an app.

---

## 1. Context

The stack today is S3 + Athena + dbt Core + Prefect Cloud Hobby + private Streamlit. Two constraints drive this review:

1. Stay cheap, and keep production running without a laptop.
2. Be able to publish a public product later without paid-subscription data leaving the private side.

Working assumptions from #302, not reopened here:

- Raw files stay in `s3://dn-lakehouse-dev`.
- Prefect Cloud Hobby + Managed work pool stays, unless this review finds a cost or capability reason to leave. It does not. See §7.
- The private draft tool and in-season tool stay on Streamlit Community Cloud.

What is actually open:

- Athena is the warehouse. DuckDB is named in the roadmap as the local-validation engine and is not wired (`dbt/profiles.yml` has Athena targets only).
- Transforms are dbt Core, run by hand. `dbt/README.md` lists no dbt Cloud jobs. Every Prefect vendor flow says the dbt Cloud trigger is not wired. After ingest, someone with a laptop runs `dbt build`.
- Apps are private Streamlit, URL-obscured, no login ([#148](../security.md), auth still [#166](https://github.com/danolen/fantasy-baseball-platform/issues/166)). Both app IAM users can read the lakehouse and query `dbt_main`, which holds every mart.

The repo is public. The leak risk is data and credentials, not source code.

---

## 2. Decision drivers

1. **No laptop in the production path.** Ingest already runs on Prefect. Transforms still do not.
2. **Cost stays at hobby scale.** ADR 0001 set a hard ceiling of < $20/mo of new spend and accepted Prefect Hobby at about $1.60/mo (Secrets Manager). This ADR does not raise that ceiling.
3. **Paid data cannot reach a public app**, including through a mart that only *displays* NFBC team totals but was *computed* from a paid projection.
4. **One warehouse.** A second hosted query engine is another copy of paid data and another bill.
5. **Operator load stays small.** Cookie rotation and hand-edited seeds are already the manual work. Do not add a scheduler that consumes the scarce Prefect budget.

---

## 3. Warehouse: Athena stays, DuckDB stays local

### Recommendation

**Athena remains the only hosted warehouse and the only production query path.** DuckDB remains the local and CI validation engine from the roadmap. Do not adopt MotherDuck or any other hosted DuckDB. Do not point Streamlit or a public site at a laptop database.

### Why Athena stays

Athena is already the reason the project can run without a machine sitting open. It is serverless, billed per scan ($5/TB, 10 MB minimum, no charge for DDL or failed queries; checked 2026-09-30 against the [Athena pricing page](https://aws.amazon.com/athena/pricing/)). These tables are fantasy CSVs and Iceberg files, not terabytes. A build plus cached Streamlit reads (15 min for rankings, 1 hour for percentiles) is cents. Provisioned capacity ($0.30/DPU-hour, reserved whether or not queries run) is the wrong product until a monthly Athena bill is large enough to care about. It is not.

S3 stays the storage layer. Iceberg tables already live there. Leaving S3 would mean re-ingesting every vendor prefix for no constraint this ticket names.

### What DuckDB is for

Local validation and, later, fixture-based checks: parse and run a slice against small parquet samples without an Athena scan. That matches the roadmap line ("DuckDB for local validation and any future ingestion staging") and the current CI shape (`dbt parse` with no AWS credentials).

DuckDB does not become the system of record. A file on a laptop cannot serve Streamlit Community Cloud or a public site, which is why Athena was chosen.

### What would actually move off Athena

Nothing in this decision. Reopen the warehouse only if one of these becomes true:

- The Athena bill is large enough that reserved capacity is cheaper than per-scan. That is a usage change, not a technology change, and it is not this dataset.
- The public product needs interactive SQL over data that a published extract cannot serve. The response to that is still Athena, behind the public schema in §6, or a published file. It is not a second warehouse.

### Rejected: hosted DuckDB (MotherDuck or similar)

A hosted DuckDB would be a second place paid files have to be copied, a sync job to operate, and another vendor account. Athena already queries the S3 files in place. The public site does not need a second SQL engine; §5 has it read a published extract.

---

## 4. Transforms: dbt Core, scheduled by GitHub Actions

The language stays dbt. The question is who runs the jobs.

### Recommendation

**Keep dbt Core in this repo. Schedule production builds with a GitHub Actions workflow on `master`, assuming an IAM role via OIDC.** The private apps keep reading `dbt_main`. Development stays in Cursor. `dbt parse` in CI stays as it is.

Do not put dbt inside Prefect. Do not buy dbt Cloud Starter.

### Why this scheduler

Production transforms are the remaining laptop step. The workflow removes it without a new vendor:

- The repo is public, so Actions minutes are not the private-repo quota.
- OIDC is already how freshness runs (`docs/security.md`). A build role is the same pattern with Glue and S3 write on the dbt schemas. The maintainer creates that role. This ADR does not.
- No long-lived AWS key in a third-party SaaS account.
- Selectors already exist: `tag:inseason+` during the season, `tag:preseason+` when draft inputs change. `dbt/README.md` warns that `tag:inseason+` still builds parent models those nodes depend on (preseason and ROS marts). Schedule for that, rather than expecting the selector to be a small slice.

Operator work that stays manual, because it is judgment rather than a cron: vendor cookie rotation, and seeds such as `faab_remaining` and player overrides. The workflow can run `dbt seed` for seeds that are already committed; it cannot invent the seed values.

### Rejected: Prefect runs dbt

ADR 0001 put flows on Prefect Cloud Hobby: **5 deployments, 500 serverless minutes/month**, custom work pools not included (rechecked 2026-09-30 against [Prefect pricing](https://www.prefect.io/pricing)). The five slots are the hello flow plus NFBC, FanGraphs, FTN, and Razzball. Razzball alone runs four times a day, Thursday through Monday. A rough in-season month is on the order of:

| Flow | Runs / month | Minutes if each run is a few minutes |
|------|----------------|--------------------------------------|
| Razzball (Thu–Mon, 4×/day) | ~80 | ~240–400 |
| NFBC + FanGraphs (daily) | ~60 | ~120–240 |
| FTN (weekends) | ~16 | ~30–50 |

That sum sits on the 500-minute cap. A daily `dbt build` of even 10 minutes would add ~300 minutes and push ingest off the free tier. Folding dbt into each vendor flow would rebuild the graph several times a day and spend the same minutes. Starter ($100/mo) is the price of more minutes and a sixth deployment. That is the spend ADR 0001 already declined.

### Fallback: dbt Cloud Developer, free, and only if Actions is the wrong place

[dbt Cloud](https://www.getdbt.com/pricing) Developer is $0: one seat, one project, job scheduling, **3,000 successful model builds per month**, and **no API**. Athena is a supported connection ([dbt Athena quickstart](https://docs.getdbt.com/guides/athena)). Starter is $100 per user per month and is what adds API access.

Use Developer only if the maintainer wants the dbt job UI badly enough to accept both of these:

- **The cap.** This project has 122 SQL models under `dbt/models/`. A daily full build is 122 × 30 = 3,660 successful model builds, over 3,000, so scheduled runs stop before the month ends. A narrower command might fit; `tag:inseason+` is not a reliable way to get there, because of the parent-model caveat above.
- **A second long-lived AWS key**, stored in dbt Cloud, because the free plan has no API and no OIDC story we use today. Prefect still could not trigger the job.

The IDE is not a reason. Models are written in Cursor, and `dbt parse` / `dbt compile` already give local feedback.

Do not take Starter for the API. There is nothing to call it with that is worth $100/mo: Prefect must not grow a dbt deployment (§ above), and Actions can run `dbt build` directly.

---

## 5. Public UI: a separate frontend over a published extract

### Recommendation

**The draft tool and the in-season tool stay on private Streamlit.** The public product is a different app. It reads a **published NFBC-only extract** (JSON or parquet written by the build to a public prefix). It does not hold AWS keys that can query Athena, and it does not run on the Streamlit apps that read `dbt_main`.

### Why not Streamlit for the public site

Streamlit is the right shape for the private tools: one operator, wide tables, a script that reruns. A public site wants the opposite.

- **Auth.** The private apps are URL-obscured with no login (#148, #166 still open). A public URL is the product. Community Cloud secrets would sit on an app anyone can open.
- **Caching and layout.** Standings and ADP change a few times a day. A file on a CDN matches that. Streamlit reruns the script and then caches in-process. It is an app shell, not a page.
- **Blast radius.** The current app IAM users can read the whole lakehouse. Hiding a widget does not fence paid marts. §6 is the fence. A public Streamlit app would still be one IAM mistake away from `mart_faab_worksheet`.

### Why the site does not query Athena live

Latency and credentials, not the per-query price. At the 10 MB minimum, even a chatty public app is a few dollars a month. Cold Athena queries are still seconds, and the app would hold keys. Publish the extract after the build. The site reads files.

### Fallback: a read API, later, still on the public schema only

If a later public feature needs interaction that a daily file cannot serve (filters that must hit fresh rows, a user-specific view), add a small read API whose role can query only the public database in §6. That API is still a separate app from Streamlit. It is not the first version.

---

## 6. Paid-data boundary: separate schema, separate prefix, separate app

### Recommendation

**Both a separate schema and a separate app.** UI hiding is not a boundary. The rule that decides membership:

> A model is public only if every ancestor is an NFBC website source (or a seed this ADR explicitly allows). If any ancestor is Razzball, FTN, FanGraphs, or another paid source, the model is paid — including when the output grain is an NFBC team or player.

Enforcement, when implemented (not in this PR):

| Layer | Private (today) | Public |
|-------|-----------------|--------|
| Glue database | `dbt_main` (apps), vendor schemas `razzball`, `ftn`, `fangraphs`, `nfbc` | New database, proposed name `dbt_public` |
| S3 | Existing lakehouse prefixes, including `razzball/`, `ftn/`, `fangraphs/` | Distinct prefix, proposed `s3://dn-lakehouse-dev/public/`. Paid Iceberg files never land here |
| IAM | `streamlit-draft-tool`, `streamlit-inseason-tool` read `dbt_main` and the lakehouse | A different principal. `s3:GetObject` on the public prefix only. Glue read on `dbt_public` only. No `dbt_main`, no vendor schema, no vendor prefix |
| App | Private Streamlit | The §5 frontend, which preferably has no AWS credentials at all and reads the published files |
| dbt | Untagged / existing tags | A `public` tag. CI fails if a `public` model’s ancestor graph reaches a paid source. This is the check that stops a later `ref()` from opening a hole |

The build role (GitHub Actions OIDC) is privileged: it has to read paid sources to build private marts. That role never ships in the public app.

### Allowed inputs

NFBC website data, as #302 lists it: ADP, standings, FAAB results, draft results, team stats. In the project today that is the `nfbc` sources (`players`, `adp`, `standings`, `in_season_players`, `in_season_overall_*`, `claims`) and, once modeled, draft results from `scripts/nfbc_draft_results.py`. All of those already live under `s3://dn-lakehouse-dev/nfbc/`.

### Not allowed, even though they sit next to NFBC rows

- **Razzball, FTN, and FanGraphs**, the whole prefix and every model downstream. Rest-of-season projections are FanGraphs. Preseason projections blend FanGraphs, Razzball, and FTN. Opening-day rosters are ingested with the FanGraphs session and stay on the paid side.
- **Derived marts.** A public page of "projected standings" would publish paid models in aggregate. `mart_projected_overall_finish` and `mart_projected_overall_finish_category` depend on ROS rankings and weekly lineup inputs. Those rankings depend on FanGraphs. Weekly projections join Razzball. The FAAB worksheet joins FTN. Dropping columns does not make them public.
- **The player-id crosswalk** (`mapping`, `stg_mpd_player_id_map`). It is an operator file and it carries Razzball and FanGraphs ids. Public pages key off the NFBC player id and name.
- **Private seeds:** `league_config`, `faab_remaining`, bid baselines, roster-slot and override seeds. They are not vendor files, and they are not website data. Contest-level NFBC standings do not need them.

### Public-safe models as of this ADR

Ancestors are NFBC website sources only:

| Model | Why it qualifies |
|-------|------------------|
| `mart_sgp_percentiles` | `src_nfbc_standings` only |
| `mart_sgp_factors` | Standings → ranked standings → SGP inputs |
| `mart_overall_category_mobility` | Overall category stats and points |

`mart_streaming_churn_evidence` is NFBC roster history joined to `league_config`. The roster history could be public; the mart as written cannot, until it no longer needs the private seed. `mart_ros_sgp_calibration` mixes NFBC category stats with operator calendar seeds and book slopes. Leave it private until a public page has a reason to show it.

Everything else in `dbt/models/main/` is paid by the ancestor rule: the six overall-rankings marts, `mart_weekly_projections`, `mart_weekly_lineup_inputs`, `mart_weekly_category_plan`, `mart_faab_worksheet`, `mart_faab_unmatched`, `mart_projection_rate_divergence`, `mart_fa_replacement_curve`, `mart_retained_core_evidence`, `mart_projected_overall_finish`, `mart_projected_overall_finish_category`.

The ancestor rule is the decision. The table is the inventory on 2026-09-30. A new mart follows the rule, not the table.

### Terms of use

This boundary is the technical fence. It does not decide whether NFBC’s terms allow a public republication of pages their site already shows. That check sits with the maintainer before the first public deploy, alongside the IAM work.

---

## 7. Cost and operator load

Checked 2026-09-30. Dollar figures are list prices, not a bill from this account.

| Piece | Decision | Monthly cost | Operator load |
|-------|----------|--------------|---------------|
| S3 + Glue catalog | Stay | Cents. Catalog stays inside the free tier at this size | None beyond ingest |
| Athena on-demand | Stay. No provisioned capacity | Cents to low dollars, including one scheduled build a day | None once the workflow exists |
| Prefect Hobby | Stay. Do not add dbt runs | $0 compute. Secrets Manager ~$1.60, per ADR 0001. 500 minutes are the real limit | Cookie rotation when a vendor flow starts failing auth |
| dbt Core + Actions | Adopt as the scheduler (follow-up) | $0 | Seeds and cookie rotation stay manual. Laptop builds stop being required |
| dbt Cloud | Do not adopt | $0 if unused. Developer is $0 but capped. Starter is $100/user | A second credential store, if the fallback is ever taken |
| Private Streamlit | Stay | $0 on Community Cloud | URL stays unpublished (#148) |
| Public site | Separate app, published files | $0–a few dollars for static hosting, chosen when that app is built | A deploy of files, not a warehouse login |
| DuckDB | Local / CI only | $0 | None in production |

Nothing here requires Prefect Starter ($100/mo) or dbt Cloud Starter ($100/user/mo). Those two upgrades are the ways this project would quietly leave hobby pricing, and neither buys a capability the recommendations need.

---

## 8. Consequences

- Private Streamlit, S3, Athena, and Prefect Hobby are unchanged.
- The production laptop step has a named replacement (Actions + OIDC). It is not built in this PR.
- A public "rankings / FAAB / projected standings" site is out of bounds until those numbers come from inputs that pass §6. Republication of NFBC ADP, standings, category totals, claims, and draft results is in bounds after the IAM prefix exists and the terms check is done.
- The 5-deployment Hobby cap is unchanged. Hello-world remains the slot to drop if a sixth *ingest* flow appears. It is not the slot to spend on dbt.

### Follow-ups (maintainer, not this PR)

1. OIDC role that can run `dbt build` into `dbt_main`, branch-scoped the way freshness already is. Document it in `docs/security.md` in the same change.
2. Actions workflow: `tag:inseason+` on a cron after the morning ingest window; `tag:preseason+` manual or offseason.
3. `dbt_public`, the `public/` prefix, the public IAM principal, and the manifest check on the `public` tag.
4. Publish step from `dbt_public` to files the frontend reads.
5. Public frontend. Blocked on 3 and 4, and on the NFBC terms check.

---

## 9. What this PR does not do

No Terraform, no new workflow, no schema, no app. Accepting the ADR is the gate #302 asked for.
