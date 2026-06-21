# `flows/` — Prefect flows

Prefect flows for vendor ingestion. See **`docs/adr/0001-prefect-on-aws.md`** for
the architecture decisions (control plane, work pool, cost) behind this package.

| File | Purpose |
|------|---------|
| `hello_flow.py` | Hello-world smoke test — writes a stamped file to `s3://dn-lakehouse-dev/_meta/prefect_hello/…` (ticket #43). |
| `nfbc_in_season.py` | NFBC in-season players download → `s3://dn-lakehouse-dev/nfbc/in-season-players/…` (ticket #44). |
| `pyproject.toml` | Package + pinned deps (`prefect` 3.x, `prefect-aws`, `boto3`, `requests`). |
| `Dockerfile` | Image for the ECS / self-hosted execution paths. |

## Run locally

No AWS, no Prefect API needed (fastest iteration):

```bash
python flows/hello_flow.py --dry-run
python flows/nfbc_in_season.py --dry-run
```

For real (needs AWS creds that can read Secrets Manager + `s3:PutObject` on the prefix):

```bash
cd flows && pip install . && cd ..
python flows/nfbc_in_season.py
```

## NFBC in-season flow (#44)

**Scope:** in-season players CSV only. Standings are tracked in #119. **dbt Cloud
job trigger is not wired in this iteration** — run `dbt build --select tag:inseason+`
manually or add a Cloud job later.

**Auth:** the flow reads `nfbc_liu` from the `fantasy-baseball-platform` secret in
`us-east-1` and pairs it with each league's `nfbc_team_id` from
[`dbt/seeds/league_config.csv`](../dbt/seeds/league_config.csv). Only `liu` +
`team_id` are required (not the full browser cookie blob).

**Rotating `nfbc_liu`:** NFBC session cookies expire or rotate when you log in
again. When the flow fails with an auth/Owner-column error:

1. Log in at [nfc.shgn.com](https://nfc.shgn.com) on your Mac.
2. DevTools → Application → Cookies → copy the `liu` value.
3. Update the `nfbc_liu` key in the `fantasy-baseball-platform` Secrets Manager
   entry.
4. Re-run the deployment.

The scheduled flow (daily 8 AM ET) may help keep that session alive between
manual logins.

**Schedule (Prefect deployment):** daily at 8:00 AM `America/New_York`.

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
#    Scope the IAM principal to:
#      - s3:PutObject on s3://dn-lakehouse-dev/nfbc/in-season-players/*
#      - secretsmanager:GetSecretValue on fantasy-baseball-platform
prefect block register -m prefect_aws
python -c "from prefect_aws import AwsCredentials; \
AwsCredentials(aws_access_key_id='AKIA...', aws_secret_access_key='...', \
region_name='us-east-1').save('fbb-aws')"
```

**Deploy and run**

```bash
prefect deploy --name hello-managed
prefect deploy --name nfbc-in-season-managed
prefect deployment run "nfbc-in-season/nfbc-in-season-managed"
```

> Hobby tier allows **5 deployments**; hello + 4 vendor flows = 5, so watch that
> cap.

## Later: Option B/C — AWS ECS / Fargate

If the project ever outgrows managed serverless, ECS/Fargate is a work-pool swap
(the flow code is unchanged) — see the ADR. That path adds a `terraform/prefect/`
module (ECR, ECS, IAM task role, CloudWatch); not built yet, by design.

## Verify the result

```bash
aws s3 ls --recursive "s3://dn-lakehouse-dev/nfbc/in-season-players/"
aws s3 ls --recursive "s3://dn-lakehouse-dev/_meta/prefect_hello/"
```
