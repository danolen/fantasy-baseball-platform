# `flows/` — Prefect flows

Prefect flows for vendor ingestion. See **`docs/adr/0001-prefect-on-aws.md`** for
the architecture decisions (control plane, work pool, cost) behind this package.

| File | Purpose |
|------|---------|
| `hello_flow.py` | Hello-world smoke test — writes a stamped file to `s3://dn-lakehouse-dev/_meta/prefect_hello/…` (ticket #43). |
| `nfbc_in_season.py` | NFBC in-season players → `s3://dn-lakehouse-dev/nfbc/in-season-players/…` (#44) plus league + overall standings → `s3://dn-lakehouse-dev/nfbc/in-season-standings/{league,overall}/…` (#119). |
| `fangraphs_ros.py` | FanGraphs ROS projections → `s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/…` (ticket #45). |
| `ftn_faab.py` | FTN FAAB recommendations → `s3://dn-lakehouse-dev/ftn/faab/…` (ticket #46). |
| `pyproject.toml` | Package + pinned deps (`prefect` 3.x, `prefect-aws`, `boto3`, `requests`). |
| `Dockerfile` | Image for the ECS / self-hosted execution paths. |

## Run locally

No AWS, no Prefect API needed (fastest iteration):

```bash
python flows/hello_flow.py --dry-run
python flows/nfbc_in_season.py --dry-run
python flows/fangraphs_ros.py --dry-run
python flows/ftn_faab.py --dry-run
```

For real (needs AWS creds that can read Secrets Manager + `s3:PutObject` on the prefix):

```bash
cd flows && pip install . && cd ..
python flows/nfbc_in_season.py
python flows/fangraphs_ros.py
python flows/ftn_faab.py
```

## FanGraphs ROS flow (#45)

**Scope:** rest-of-season hitting + pitching projections for all seven systems
(`atc`, `depthcharts`, `oopsy`, `steamer`, `thebat`, `thebat-x`, `zips`).
**dbt Cloud job trigger is not wired in this iteration.**

The flow calls FanGraphs' internal `/api/projections` endpoint (the same data
behind the on-page **Data Export** button) and writes CSVs whose headers match
the manual export layout (`flows/templates/fangraphs_ros_*_header.csv`).

**Auth:** add a `fangraphs_cookie` key to the `fantasy-baseball-platform`
secret. Value is the full WordPress session cookie, e.g.
`wordpress_logged_in_<hash>=dnolen%7C...` (DevTools → Application → Cookies →
copy **name**=`value` for the `wordpress_logged_in_*` entry). Analytics cookies
and `fg_is_member` are not required for the API path this flow uses.

**Rotating `fangraphs_cookie`:** when the flow fails with a FanGraphs auth /
missing-`playerid` error, log in at [fangraphs.com](https://www.fangraphs.com),
copy a fresh `wordpress_logged_in_*` cookie, and update the secret.

**Schedule (Prefect deployment):** daily at 8:00 AM `America/New_York` (same as
NFBC). S3 date partitions use `America/New_York`.

## FTN FAAB flow (#46)

**Scope:** 12- and 15-team FAAB CSVs only. FTN projections are tracked in #122.
**dbt Cloud job trigger is not wired in this iteration.**

Source pages:

- [12-team FAAB](https://ftnfantasy.com/fantasy/mlb/12-team-faab)
- [15-team FAAB](https://ftnfantasy.com/fantasy/mlb/15-team-faab)

Upload filenames match manual exports in `data/ftn/faab/`:

- `12 Team FAAB 2026.csv`
- `15 team faab 2026.csv`

**Auth:** add these keys to the `fantasy-baseball-platform` secret (values only,
not `name=value` — the flow builds the cookie header):

| Key | Source (DevTools → Application → Cookies → `.ftnfantasy.com`) |
|-----|------------------------------------------------------------------|
| `ftn_refresh_token` | `refresh_token` |
| `ftn_access_token` | `access_token` |
| `ftn_user_id` | `user_id` |

All three are required. FTN refresh (`POST /users/token/refresh`) needs **both**
JWT cookies; the FAAB table page also requires a valid `access_token`.

The flow fetches each FAAB page with browser impersonation (`curl_cffi`) and
parses the embedded wpDataTables HTML (same data behind the on-page **CSV**
button), then writes CSVs with quoted headers matching manual exports.

**Rotating FTN tokens:** when the flow fails with an auth error, log in at
[ftnfantasy.com](https://ftnfantasy.com), copy fresh `refresh_token`,
`access_token`, and `user_id`, and update the secret.

**Schedule (Prefect deployment):** Saturdays and Sundays at 8:00 AM
`America/New_York`. S3 date partitions use `America/New_York`.

## NFBC in-season flow (#44, #119)

**Scope:** in-season players CSV (#44) plus league and overall standings (#119).
**dbt Cloud job trigger is not wired in this iteration** — run
`dbt build --select tag:inseason+` manually or add a Cloud job later.

Per run, for each league in
[`dbt/seeds/league_config.csv`](../dbt/seeds/league_config.csv) the flow uploads:

| Item | S3 prefix | Scope |
|------|-----------|-------|
| Players | `nfbc/in-season-players/…/<league>.csv` | all leagues |
| League standings | `nfbc/in-season-standings/league/…/<league>.csv` | all leagues |
| Overall standings | `nfbc/in-season-standings/overall/…/<league>.csv` | leagues with `nfbc_overall_game_type_id` |

Overall (contest-wide) standings are scoped to leagues whose
`nfbc_overall_game_type_id` is set in the seed — today `nolen_oc` (Online
Championship, `890`) and `nolen_50` (NFBC 50, `897`).

> **Standings ingestion is gated OFF by default** (`include_standings=False`,
> opt in with `--with-standings`). NFBC has **no authenticated standings CSV
> endpoint** like players' `api/react/players_download`: the React standings UI
> builds tables client-side from access-controlled CDN JSON, and the legacy
> `standings_download.php` path returns the SPA shell (HTTP 403) with the `liu`
> cookie. The maintainer currently produces standings CSVs by copy-pasting the
> rendered page.
>
> **To re-enable:** rework `download_standings_csv` to POST the legacy
> `standings.data.php` / `standings_overall.data.php` endpoints with the **full**
> NFBC session cookie (not just `liu`) and parse the returned HTML table into
> CSV, then flip the default back on. Tracking in #119.

**Auth:** the flow reads `nfbc_liu` from the `fantasy-baseball-platform` secret in
`us-east-1` and pairs it with each league's `nfbc_team_id` from
[`dbt/seeds/league_config.csv`](../dbt/seeds/league_config.csv). Only `liu` +
`team_id` are required (not the full browser cookie blob). League standings are
scoped by the `team_id` cookie (like players); overall standings are scoped by
`nfbc_overall_game_type_id`.

**Rotating `nfbc_liu`:** NFBC session cookies expire or rotate when you log in
again. When the flow fails with an auth/Owner-column error:

1. Log in at [nfc.shgn.com](https://nfc.shgn.com) on your Mac.
2. DevTools → Application → Cookies → copy the `liu` value.
3. Update the `nfbc_liu` key in the `fantasy-baseball-platform` Secrets Manager
   entry.
4. Re-run the deployment.

The scheduled flow (daily 8 AM ET) may help keep that session alive between
manual logins.

**Schedule (Prefect deployment):** daily at 8:00 AM `America/New_York`. S3
date partitions (`year=/month=/day=`) also use `America/New_York` so a run at
8 AM ET and a manual upload the same calendar day land in the same folder.

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
#      - s3:PutObject on s3://dn-lakehouse-dev/nfbc/in-season-standings/*
#      - s3:PutObject on s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/*
#      - s3:PutObject on s3://dn-lakehouse-dev/ftn/faab/*
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
prefect deploy --name fangraphs-ros-managed
prefect deploy --name ftn-faab-managed
prefect deployment run "nfbc-in-season/nfbc-in-season-managed"
prefect deployment run "fangraphs-ros/fangraphs-ros-managed"
prefect deployment run "ftn-faab/ftn-faab-managed"
```

> Hobby tier allows **5 deployments**; hello + nfbc + fangraphs + ftn = 4
> vendor flows — still within the cap.

## Later: Option B/C — AWS ECS / Fargate

If the project ever outgrows managed serverless, ECS/Fargate is a work-pool swap
(the flow code is unchanged) — see the ADR. That path adds a `terraform/prefect/`
module (ECR, ECS, IAM task role, CloudWatch); not built yet, by design.

## Verify the result

```bash
aws s3 ls --recursive "s3://dn-lakehouse-dev/nfbc/in-season-players/"
aws s3 ls --recursive "s3://dn-lakehouse-dev/nfbc/in-season-standings/"
aws s3 ls --recursive "s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/"
aws s3 ls --recursive "s3://dn-lakehouse-dev/ftn/faab/"
aws s3 ls --recursive "s3://dn-lakehouse-dev/_meta/prefect_hello/"
```
