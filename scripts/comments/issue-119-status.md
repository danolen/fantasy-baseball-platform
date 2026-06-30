## Status update (2026-06-24)

Standings ingest is **implemented and merged** into the existing `nfbc-in-season` Prefect flow (`flows/nfbc_in_season.py`, deployment `nfbc-in-season-managed`). Issue remains open because **league standings are not reliably running on Prefect Managed** due to Cloudflare bot protection on the league path.

---

### Completed

**Flow & deployment** ([#128](https://github.com/danolen/fantasy-baseball-platform/pull/128), [#131](https://github.com/danolen/fantasy-baseball-platform/pull/131), [#132](https://github.com/danolen/fantasy-baseball-platform/pull/132), [#134](https://github.com/danolen/fantasy-baseball-platform/pull/134))

- Extended `nfbc-in-season` to ingest standings alongside in-season players for every league in `dbt/seeds/league_config.csv`.
- NFBC has no standings CSV export — the flow POSTs the same legacy HTML endpoints the site uses (`standings.data.php`, `standings_overall.data.php`) and parses tables into CSV.
- **League standings** (all 5 leagues): summary table `#standings_league` plus hitters/pitchers breakdown → one wide CSV with all 10 roto category stats and points (`R`, `R_pts`, … `WHIP`, `WHIP_pts`).
- **Overall standings** (`nolen_oc`, `nolen_50` only — leagues with `nfbc_overall_game_type_id` in the seed): three views per contest matching the site dropdown — `overview`, `category-stats`, `category-points`.
- S3 layout (NY-date partitions, `America/New_York`):
  - `s3://dn-lakehouse-dev/nfbc/in-season-standings/league/year=/month=/day=/<league>.csv`
  - `s3://dn-lakehouse-dev/nfbc/in-season-standings/overall/{overview,category-stats,category-points}/year=/month=/day=/<league>.csv`
- Auth via Secrets Manager: `nfbc_liu` (players + all standings), `nfbc_jwt` (league standings only). Values-only secrets; flow builds `Cookie` headers per endpoint.
- Per-league failure isolation preserved; auth errors fail loudly; `--skip-players` / `--skip-standings` flags for partial runs.
- Operator docs in `flows/README.md` (cookie rotation, S3 layout, schedule).

**Acceptance criteria mapping**

| Criterion | Status |
|-----------|--------|
| Standings downloaded per league via NFBC session cookies | ✅ Implemented (HTML scrape, not a native CSV download) |
| Uploads to dated S3 prefix | ✅ Done — path is `nfbc/in-season-standings/…` (not the original `nfbc/standings/…` spec) |
| Cookie expiry / HTML login-page → loud failure | ✅ Done for auth errors; Cloudflare 403 now has an explicit error message ([#134](https://github.com/danolen/fantasy-baseball-platform/pull/134)) |
| Per-league failure isolation | ✅ Done |
| dbt Cloud job trigger after players + standings | ⏳ Deferred — documented in `flows/README.md`; run `dbt build --select tag:inseason+` manually for now |

---

### Pending / blocked

1. **League standings on Prefect Managed (Cloudflare)** — the main blocker keeping this issue open.
   - Overall standings and players succeed on Managed compute with `nfbc_liu` alone.
   - League standings POST to `standings.data.php` returns HTTP 403 with `Cf-Mitigated: challenge` from Cloudflare (datacenter IP cannot pass the interactive challenge). This is **not** fixed by rotating `jwt`.
   - Confirmed in production run (2026-06-24) and reproduced from cloud/debug sessions.
   - **Workarounds today:** upload league standings manually (`utils/upload_folder_to_s3.py`), or run the flow from a non-datacenter IP.
   - **Durable fix needed:** move league standings ingest off Prefect Managed (ECS/Fargate with stable egress?), browser automation, or ask NFBC to relax CF on `/standings` / `standings.data.php`.

2. **dbt Cloud job trigger** — still not wired; deferred until a production in-season job exists.

3. **S3 path vs original AC** — uploads go to `nfbc/in-season-standings/` rather than `nfbc/standings/`. Confirm whether downstream dbt sources expect the new path or need a follow-up ticket.

---

### Suggested close criteria

- [ ] League standings ingest runs reliably on scheduled Prefect deployment (or an agreed alternative compute path is in place and documented).
- [ ] dbt Cloud trigger decision made (wire it up or explicitly defer with a linked issue).
