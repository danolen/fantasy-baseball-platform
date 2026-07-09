# `flows/` — Prefect flows

Prefect flows for vendor ingestion. See **`docs/adr/0001-prefect-on-aws.md`** for
the architecture decisions (control plane, work pool, cost) behind this package.

| File | Purpose |
|------|---------|
| `hello_flow.py` | Hello-world smoke test — writes a stamped file to `s3://dn-lakehouse-dev/_meta/prefect_hello/…` (ticket #43). |
| `nfbc_in_season.py` | NFBC in-season players → `s3://dn-lakehouse-dev/nfbc/in-season-players/…` (#44) plus league + overall standings → `s3://dn-lakehouse-dev/nfbc/in-season-standings/{league,overall}/…` (#119). |
| `fangraphs_ros.py` | FanGraphs ROS projections → `s3://dn-lakehouse-dev/fangraphs/projections/rest-of-season/…` (ticket #45). |
| `ftn_faab.py` | FTN FAAB recommendations → `s3://dn-lakehouse-dev/ftn/faab/…` (ticket #46). |
| `razzball_weekly.py` | Razzball weekly + weekend projections → `s3://dn-lakehouse-dev/razzball/projections/weekly/…` (ticket #47). |
| `pyproject.toml` | Package + pinned deps (`prefect` 3.x, `prefect-aws`, `boto3`, `requests`). |
| `Dockerfile` | Image for the ECS / self-hosted execution paths. |

## Run locally

No AWS, no Prefect API needed (fastest iteration):

```bash
python flows/hello_flow.py --dry-run
python flows/nfbc_in_season.py --dry-run
python flows/fangraphs_ros.py --dry-run
python flows/ftn_faab.py --dry-run
python flows/razzball_weekly.py --dry-run
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
`access_token`, and `user_id`, and update the secret. The flow refreshes the
access token automatically when the JWT is near expiry; if refresh fails (FTN
sometimes returns HTTP 500 for a stale refresh token) it continues with the
stored cookies — refresh failure alone is not fatal until a download fails.

**Schedule (Prefect deployment):** Saturdays and Sundays at 8:00 AM
`America/New_York`. S3 date partitions use `America/New_York`.

## Razzball weekly flow (#47)

**Scope:** weekly hitting, weekly pitching, and weekend hitting projections.
There is no weekend pitching export.

| Slice | Page | S3 prefix | Filename |
|-------|------|-----------|----------|
| Weekly hitting | [hittertron-nextweek](https://razzball.com/hittertron-nextweek/) | `razzball/projections/weekly/hitting/` | `hittertron.csv` |
| Weekly pitching | [streamers-nextweek](https://razzball.com/streamers-nextweek/) | `razzball/projections/weekly/pitching/` | `streamonator.csv` |
| Weekend hitting | [hittertron-nextfriday-sunday](https://razzball.com/hittertron-nextfriday-sunday/) | `razzball/projections/weekly/weekend_hitting/` | `hittertron.csv` |

Razzball has no server CSV URL — the on-page **Get CSV** button serializes
table `#neorazzstatstable` in the browser. The flow fetches the subscriber HTML
with `curl_cffi` and parses that table into CSV (matching manual exports in
`data/razzball/projections/weekly/`).

**Auth:** add `razzball_cookie` to the `fantasy-baseball-platform` secret.
Paste the two WordPress cookies from DevTools (logged-in session on any
subscriber tool page):

```
wordpress_logged_in_...=...; wordpress_sec_...=...
```

Analytics / Cloudflare cookies are not required.

**Schedules (two deployments, one flow):**

| Deployment | Slices | Cron (America/New_York) |
|------------|--------|-------------------------|
| `razzball-weekly-managed` | weekly hitting + pitching | 8 AM, 12 PM, 4 PM, 8 PM on Sat, Sun, Mon |
| `razzball-weekend-managed` | weekend hitting only | 8 AM, 12 PM, 4 PM, 8 PM on Thu, Fri |

CLI flags: `--weekly-hitting-only`, `--weekly-pitching-only`,
`--weekend-hitting-only`.

## NFBC in-season flow (#44, #119)

**Scope:** in-season players CSV (#44) plus league and overall standings (#119).
**dbt Cloud job trigger is not wired in this iteration** — run
`dbt build --select tag:inseason+` manually or add a Cloud job later.

Per run, for each league in
[`dbt/seeds/league_config.csv`](../dbt/seeds/league_config.csv) the flow uploads:

| Item | S3 prefix | Scope |
|------|-----------|-------|
| Players | `nfbc/in-season-players/…/<league>.csv` | all leagues |
| League standings | `nfbc/in-season-standings/league/…/<league>.csv` | all leagues — summary + 10 roto category stats/points in one wide CSV |
| Overall overview | `nfbc/in-season-standings/overall/overview/…/<league>.csv` | leagues with `nfbc_overall_game_type_id` |
| Overall category stats | `nfbc/in-season-standings/overall/category-stats/…/<league>.csv` | same |
| Overall category points | `nfbc/in-season-standings/overall/category-points/…/<league>.csv` | same |

Overall (contest-wide) standings are scoped to leagues whose
`nfbc_overall_game_type_id` is set in the seed — today `nolen_oc` (Online
Championship, `890`) and `nolen_50` (NFBC 50, `897`). Each contest uploads
**three** overall views matching the [standings_overall](https://nfc.shgn.com/standings_overall)
dropdown: overview, category stats (`view_type=stats`), and category points
(`view_type=points`). League standings use each league's `nfbc_league_id` (also
in the seed). Use `--skip-players`, `--skip-standings`,
`--skip-league-standings`, or `--skip-overall-standings` to run a subset.

**How standings work:** NFBC has no standings CSV export, so the flow POSTs the
same legacy endpoints the standings pages use and parses the returned HTML table
into CSV (the equivalent of copy-pasting the rendered page):

- League: `POST standings.data.php` with `league_id` (summary table
  `#standings_league` plus hitters/pitchers breakdown tables parsed into one wide
  CSV with `R`, `R_pts`, `HR`, `HR_pts`, … `WHIP`, `WHIP_pts`).
- Overall: `POST standings_overall.data.php` with `game_type_id` and `view_type`
  (`overview`, `stats`, `points`) → table `#standings_overall_1`.

These legacy endpoints use only the session cookies below — analytics cookies
(`_ga`, `_gid`, etc.) are not required.

**Auth:** add these keys to the `fantasy-baseball-platform` secret (values only,
not `name=value` — the flow builds the cookie header):

| Key | Required for | Source (DevTools → Application → Cookies → `nfc.shgn.com`) |
|-----|--------------|--------------------------------------------------------------|
| `nfbc_liu` | players, all standings | `liu` |
| `nfbc_jwt` | league standings only | `jwt` |

Players send `liu` plus each league's `nfbc_team_id` from the seed. Overall
(contest-wide) standings need only `liu`. League standings also need `jwt` from
the same browser session as `liu` (copy both values after logging in).

Analytics / tracking cookies are not used. Do **not** paste the full `Cookie`
request header into Secrets Manager.

**Rotating NFBC cookies:** when the flow fails with an auth error (missing Owner
column for players, or a missing standings table):

1. Log in at [nfc.shgn.com](https://nfc.shgn.com).
2. DevTools → Application → Cookies → `nfc.shgn.com` → copy the **Value** for
   `liu` (and `jwt` if league standings fail).
3. Update `nfbc_liu` / `nfbc_jwt` in the `fantasy-baseball-platform` Secrets
   Manager entry.
4. Re-run the deployment.

Copy cookie **values only** — not URL-encoded wrappers like `%22eyJ...%22` and
not `name=value` prefixes. The flow normalizes those when present, but raw
values from DevTools are safest.

**League standings HTTP 403 (Cloudflare):** scripted POSTs to
`standings.data.php` fail with `cf-mitigated: challenge` from **both** Prefect
Managed and residential Macs — Cloudflare blocks non-browser clients on that
path. Overall standings and players are unaffected. The
`nfbc-in-season-managed` deployment sets `include_league_standings: false`.

**Refresh league standings from browser HTML (supported path):**

1. Log in at [nfc.shgn.com/standings](https://nfc.shgn.com/standings).
2. For each league in `dbt/seeds/league_config.csv`, open DevTools → **Network**,
   select the league, find the `standings.data.php` request, open **Response**,
   and save the body as `<league>.html` (e.g. `nolen_oc.html`).
3. Put those files in one directory, then from the repo root:

```bash
source venv/bin/activate
python flows/nfbc_in_season.py --league-standings-from-html ./nfbc_standings_html/
```

That parses each HTML file with the same table logic as the live POST and
uploads to `s3://dn-lakehouse-dev/nfbc/in-season-standings/league/year=/month=/day=/`.
No Secrets Manager / NFBC cookies are needed for this path (only AWS for S3).
Use `--dry-run` to preview keys without uploading.

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
#      - s3:PutObject on s3://dn-lakehouse-dev/razzball/projections/weekly/*
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
prefect deploy --name razzball-weekly-managed
prefect deploy --name razzball-weekend-managed
prefect deployment run "nfbc-in-season/nfbc-in-season-managed"
prefect deployment run "fangraphs-ros/fangraphs-ros-managed"
prefect deployment run "ftn-faab/ftn-faab-managed"
prefect deployment run "razzball-weekly/razzball-weekly-managed"
prefect deployment run "razzball-weekly/razzball-weekend-managed"
```

> Hobby tier allows **5 deployments**. Undeploy `hello-world/hello-managed`
> before deploying both Razzball flows — nfbc + fangraphs + ftn +
> razzball-weekly + razzball-weekend fills the cap.

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
