# `flows/` — Prefect flows

Prefect flows for vendor ingestion. See **`docs/adr/0001-prefect-on-aws.md`** for
the architecture decisions (control plane, work pool, cost) behind this package.

| File | Purpose |
|------|---------|
| `hello_flow.py` | Hello-world smoke test — writes a stamped file to `s3://dn-lakehouse-dev/_meta/prefect_hello/…` (ticket #43). |
| `pyproject.toml` | Package + pinned deps (`prefect` 3.x, `prefect-aws`, `boto3`). |
| `Dockerfile` | Image for the ECS / self-hosted execution paths. |

## Run locally

No AWS, no Prefect API needed (fastest iteration):

```bash
python flows/hello_flow.py --dry-run
```

For real (needs AWS creds that can `s3:PutObject` on the prefix):

```bash
cd flows && pip install . && cd ..
python flows/hello_flow.py
```

## Deploy to Prefect Cloud

First, once per machine: `prefect cloud login`.

### Option A — Prefect Managed serverless (free Hobby tier, recommended start)

No AWS infra. Give the flow S3 access via an `AWSCredentials` block (created in
the Prefect UI or CLI) referenced from the deployment.

```bash
prefect work-pool create --type prefect:managed managed-pool
prefect deploy flows/hello_flow.py:hello_world \
  --name hello-managed --pool managed-pool --cron "0 12 * * *"
prefect deployment run "hello-world/hello-managed"
```

### Option B/C — AWS ECS / Fargate

Requires the paid Starter plan (push pool) **or** a self-hosted Prefect Server —
see the ADR. The flow code does not change; only the work pool does. The
`terraform/prefect/` module for this path is deferred until the open decision in
the ADR (§7) is made.

## Verify the result

```bash
aws s3 ls --recursive "s3://dn-lakehouse-dev/_meta/prefect_hello/"
```
