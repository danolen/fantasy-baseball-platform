# ADR 0001 — Prefect on AWS for vendor ingestion flows

- **Status:** Accepted — **Option A** (Prefect Cloud Hobby + Prefect Managed serverless). Decided 2026-06-16; see [§7](#7-decision-resolved-option-a).
- **Date:** 2026-06-16
- **Ticket:** #43 (A4.1)
- **Depends on:** nothing
- **Blocks:** #44, #45, #46, #47 (the A4.x vendor flows)

---

## 1. Context

We need a scheduler/orchestrator to run a handful of small Python jobs that log
into fantasy-baseball vendors (NFBC, FanGraphs, FTN, Razzball), download files,
drop them in `s3://dn-lakehouse-dev/...`, and then trigger a dbt Cloud job. That
is the whole job: **a few flows, on daily/weekly crons, each running for a couple
of minutes.**

This is a **hobby project** with a hard **cost ceiling of < $20/mo** of *new*
AWS/SaaS spend. We want to learn **Prefect** and **AWS ECS/Fargate** (both are
on a job description the maintainer is targeting). The only prior orchestration
experience is **AWS-managed Airflow (MWAA)**, so this ADR is written to map
Prefect concepts onto Airflow/MWAA ones.

### Why not just keep using MWAA / Airflow?

MWAA is an *always-on* environment: even the smallest `mw1.small` environment
bills roughly **$0.49/hr just for the environment** (~$350+/mo) before workers,
plus a VPC. For four tiny cron jobs that run a few minutes a day, that is
absurd. The whole point of this ticket is to get orchestration that is
**serverless / scale-to-zero** so a hobby project pays cents, not hundreds.

### Concept map (for an Airflow/MWAA brain)

| Airflow / MWAA | Prefect | Notes |
|---|---|---|
| DAG | **Flow** (`@flow`) | A Python function, not a YAML/`DAG()` object. |
| Task / Operator | **Task** (`@task`) | Plain Python; no operator zoo. Calls are just function calls. |
| `schedule_interval` (cron) | **Deployment schedule** (cron or interval) | Lives on the *deployment*, not in code. |
| Scheduler + Webserver + Metadata DB (MWAA-managed) | **Prefect Cloud API/UI** (or self-hosted Prefect Server) | The "control plane". Cloud Hobby tier is free. |
| Executor (Local/Celery/Kubernetes) + Workers | **Work pool** + **Worker** | The "data plane" — *where* a flow run actually executes. |
| Worker autoscaling | **Push work pool** (no worker process) or a **worker** polling a pull pool | Push pools let Prefect Cloud launch ECS tasks directly — no always-on worker. |
| Connections / Variables / Secrets backend | **Blocks** + **AWS Secrets Manager** | Vendor cookies/creds go in Secrets Manager; a Prefect Block points at them. |
| Deploy = sync `dags/` to the S3 bucket | `prefect deploy` (build Docker image → ECR, register deployment) | Code ships as a **container image**, not a synced folder. |
| Retries / SLAs / alerting | Task `retries=`, **Automations** (email/Slack/webhook on failure) | Failure notifications are first-class. |

The single most important idea: **Prefect splits the control plane (scheduling,
UI, run history) from the data plane (the compute that runs your code).** You
choose them independently. That decoupling is what makes the decisions below
"pick a control plane" + "pick a data plane" rather than one monolithic choice.

---

## 2. Decision drivers

1. **Cost ≤ $20/mo new spend** (hard ceiling).
2. **Minimize operational burden** (it's a hobby; we don't want to babysit a
   database/webserver).
3. **Learn Prefect + ECS/Fargate** (explicit goal — pushes us *toward* using
   real ECS at some point, even though it isn't the cheapest option).
4. **Avoid over-engineering** — four cron jobs do not need a Kubernetes cluster,
   a Terraform Cloud org, or a multi-AZ Postgres.
5. **Credentials never in git** — vendor cookies/creds live in AWS Secrets
   Manager only.

---

## 3. Decision: control plane (orchestration)

**Use Prefect Cloud — Hobby (free) tier — for the control plane.**

- **Prefect Cloud (free) vs. self-hosted Prefect Server:** Self-hosting Prefect
  Server means running a web/API server **and its database 24/7** somewhere
  (another container to patch, back up, and pay for). For a hobby project that
  is exactly the operational burden we are trying to escape from MWAA. The Cloud
  Hobby tier gives us the UI, scheduler, run history, logs, and 5 automations
  for **$0**, with no server to run.
- The Hobby tier limits that matter to us: **1 workspace, 5 deployments, 2 users,
  5 automations, 7-day run-history retention.** We have ~5 flows (1 hello + 4
  vendors), which fits the 5-deployment cap *exactly* — something to watch as a
  future constraint, not a blocker today.

> ⚠️ **The catch that drives everything below:** As of 2026, the Prefect Cloud
> **Hobby (free) tier can only run flows on *Prefect-managed Serverless* compute
> (500 minutes/month included). "Custom" work pools — which includes the AWS ECS
> *push* work pool — require the paid Starter plan ($100/mo) or above.** This is
> a pricing change from older Prefect, where ECS push pools were usable on the
> free tier. It is the crux of this ADR.

Sources checked 2026-06-16: Prefect pricing page and Prefect's "two new
self-serve plans" announcement ("Our Hobby tier uses Serverless exclusively,
while Starter and Team plans let you choose between Serverless, your own
infrastructure, or a hybrid approach").

---

## 4. Decision: data plane (where flows run) — the options

Because of the catch above, "free Prefect Cloud" and "run on my own ECS/Fargate"
are **mutually exclusive** under current pricing. Here are the realistic options.

### Option A — Prefect Cloud Hobby + **Prefect Managed (Serverless)** work pool
Prefect runs the container on *their* infra. We provide an `AWSCredentials`
block so the flow can write to our S3.

- ➕ **$0.** Zero AWS infra to build. Fastest path to a working hello-world.
- ➕ Removes all server/worker ops (the ticket's stated preference).
- ➕ Teaches the **Prefect** half of the goal fully (flows, deployments,
  schedules, blocks, secrets, automations, retries).
- ➖ Teaches **none of the ECS/Fargate** half — no ECR, no task role, no
  CloudWatch, no VPC. The container runs in Prefect's account, not ours.
- ➖ 500 serverless-min/month cap (plenty for our cadence — see costs).

### Option B — Prefect Cloud **Starter** + **AWS ECS push** work pool
Prefect Cloud launches Fargate tasks **in our AWS account** with no worker to
run. `prefect work-pool create --type ecs:push --provision-infra` scaffolds the
IAM user/role, ECS cluster, VPC, and ECR for you.

- ➕ Best-in-class managed UI **and** real ECS/Fargate in our account (ECR, task
  role, CloudWatch) — hits *both* learning goals cleanly.
- ➕ Push pool = **no always-on worker** → AWS compute cost is just per-run
  pennies.
- ➖ **$100/mo** Starter plan — **5× over the $20 ceiling.** Disqualifying unless
  the ceiling is raised.

### Option C — **Self-hosted Prefect Server (OSS)** + **ECS** work pool, all in our AWS
Prefect OSS has **no tier limits** (unlimited deployments/work pools/
automations). Run the server on a small Fargate service; run flows as ECS tasks.

- ➕ **No Prefect license cost** + real ECS/Fargate in our account → satisfies
  the $20 ceiling **and** the ECS learning goal.
- ➕ Unlimited deployments (escapes the 5-deployment Hobby cap).
- ➖ We now run the **Prefect Server + its DB + a worker** ourselves. An
  always-on Fargate service for the server (and a worker, unless we use a push
  setup) is the main cost and ops driver here (~$12–18/mo, at the edge of the
  ceiling — see costs). This is the operational burden the ticket wanted to
  avoid, taken on **deliberately as the ECS learning exercise**.

### Why not Kubernetes / EC2 workers / Terraform Cloud / multi-AZ Postgres?
All over-engineering for four cron jobs. Explicitly out of scope (driver #4).

---

## 5. Recommendation: a two-step path (avoids throwaway work)

The flow **code is identical across A, B, and C** — only the *deployment target*
(work pool) changes. That means we can start cheap and add ECS later with **zero
rewrite**. So:

1. **Step 1 — ship now (this ticket): Option A.** Get the hello-world flow
   running end-to-end on **Prefect Cloud Hobby + Managed serverless** for **$0**,
   no AWS infra. This nails the Prefect half of the learning goal and unblocks
   #44–#47 immediately (vendor flows can be written and scheduled today).
2. **Step 2 — deliberate ECS spike (follow-up): Option C.** When the ECS/Fargate
   résumé skill is the focus, stand up the self-hosted server + ECS work pool via
   a small `terraform/prefect/` module (ECR repo, ECS cluster/service, IAM **task
   role** with S3 + Secrets Manager access, CloudWatch log group). Re-point the
   same deployments at the ECS work pool. Keep this module separate from the
   `terraform/aws/` import module (#54) to keep blast radius small.

This phasing **is** the anti-over-engineering answer: don't build AWS infra until
the flow is proven on free compute, then add ECS on purpose as a learning step.

---

## 6. Cost analysis (vs. the < $20/mo ceiling)

Cadence assumption: 1 hello (manual/daily) + 4 vendor flows (daily/weekly), each
1–3 min/run. Call it **~120 flow-runs/month, ~250 compute-minutes/month**.

| Component | Option A (recommended now) | Option C (ECS spike) |
|---|---|---|
| Prefect Cloud | **$0** (Hobby) | **$0** (OSS self-host) |
| Prefect Serverless minutes | ~250 of 500 free → **$0** | n/a |
| Fargate compute | n/a | per-run: 0.25 vCPU/0.5 GB ≈ **$0.004/run** → **< $1/mo** |
| Always-on Fargate (server + worker) | n/a | ~0.5 vCPU/1 GB 24/7 ≈ **$12–18/mo** ⚠️ |
| ECR storage | n/a | ~500 MB image ≈ **$0.05/mo** |
| CloudWatch logs | n/a | a few MB ≈ **~$0/mo** |
| Secrets Manager | $0.40/secret/mo × ~4 = **~$1.60/mo** | **~$1.60/mo** |
| **Total new spend** | **≈ $1.60/mo** ✅ | **≈ $14–20/mo** ⚠️ (at the ceiling) |
| Disqualified: Option B (Starter) | — | **$100/mo** ❌ |

Takeaways:
- **Option A is ~$1.60/mo** (just Secrets Manager) and well inside the ceiling.
- **Option C fits the ceiling only because the *server* compute is the cost** —
  the actual flow runs are pennies. To stay safely under $20, run the server +
  worker in **one** small Fargate task, or scale the service to zero when idle.
- **Option B blows the ceiling 5×** and is only worth it if the maintainer
  decides the managed-ECS experience is worth $100/mo.

---

## 7. Decision (resolved): Option A

The ticket's acceptance criterion *"hello-world flow runs on Fargate"* assumed
free-tier ECS push, which **no longer exists**. The maintainer chose
**Option A**: if Prefect Managed serverless serves the project's needs, ECS/
Fargate is unnecessary. We may move to Option C later, but likely never need to.

**Consequences of this choice:**
- The "runs on Fargate" AC is **amended to "runs via Prefect Cloud Managed
  serverless"** for #43.
- `terraform/prefect/` stays **unbuilt** (it only exists for the ECS paths).
- Deployment is config-only via `prefect.yaml` (a Managed work pool) — no Docker
  image or ECR needed. The `flows/Dockerfile` is kept for a possible future
  Option C spike but is not on the critical path.

---

## 8. What ships in this PR (decision-independent scaffolding)

- `flows/` — a `pyproject.toml`-managed package for all Prefect flows.
- `flows/hello_flow.py` — the hello-world flow: writes a stamped file to
  `s3://dn-lakehouse-dev/_meta/prefect_hello/year=/month=/day=/<timestamp>.txt`.
  Runs locally with `--dry-run` (no AWS/Prefect needed) for fast iteration.
- `flows/Dockerfile` — kept for a future Option C spike; not used by Option A.
- `prefect.yaml` (repo root) — the Managed-pool deployment for the hello-world
  flow (Option A): git-clone pull step + `pip_packages`, no image build.
- `flows/README.md` — how to run locally and the Option A deploy/run cycle.

**Deferred (Option C only):** `terraform/prefect/` (ECR + ECS + IAM task role +
CloudWatch). Building it now would mean implementing infra for an architecture
we deliberately did not choose — the over-engineering driver #4 warns against.

---

## 9. Consequences

- ➕ A working hello-world and a clean flows package unblock #44–#47 now.
- ➕ Switching A → C later is a work-pool/`prefect deploy` change, not a rewrite.
- ➖ With Option A we don't touch ECS yet — the ECS/Fargate learning goal is
  deferred to the Step 2 spike.
- ⚠️ The 5-deployment Hobby cap will bind once all four vendor flows exist
  (4 + hello = 5). Revisit if a 6th flow appears (drop hello, or move to OSS).
