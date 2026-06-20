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

## Deploy to Prefect Cloud (Option A — Managed serverless, the accepted path)

This is the architecture chosen in the ADR: Prefect Cloud Hobby (free) + a
Prefect Managed work pool. No AWS infra, no Docker/ECR. Deployment config lives
in `prefect.yaml` at the repo root.

**One-time setup**

```bash
# 1. Log in to Prefect Cloud (Hobby tier).
prefect cloud login

# 2. Create the managed (serverless) work pool referenced by prefect.yaml.
prefect work-pool create --type prefect:managed managed-pool

# 3. Store AWS creds so the flow can write to S3 from managed compute.
#    Use an IAM principal scoped to PutObject on s3://dn-lakehouse-dev/_meta/*.
prefect block register -m prefect_aws
python -c "from prefect_aws import AwsCredentials; \
AwsCredentials(aws_access_key_id='AKIA...', aws_secret_access_key='...', \
region_name='us-east-1').save('fbb-aws')"
```

**Deploy and run** (the AC's `prefect deploy` + `prefect deployment run` cycle):

```bash
prefect deploy --name hello-managed
prefect deployment run "hello-world/hello-managed"
```

> Hobby tier allows **5 deployments**; hello + 4 vendor flows = 5, so watch that
> cap. The deployment is unscheduled by default to conserve the 500 free
> serverless minutes/month — trigger it manually.

## Later: Option B/C — AWS ECS / Fargate

If the project ever outgrows managed serverless, ECS/Fargate is a work-pool swap
(the flow code is unchanged) — see the ADR. That path adds a `terraform/prefect/`
module (ECR, ECS, IAM task role, CloudWatch); not built yet, by design.

## Verify the result

```bash
aws s3 ls --recursive "s3://dn-lakehouse-dev/_meta/prefect_hello/"
```
