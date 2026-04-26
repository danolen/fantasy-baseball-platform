---
name: cross-league FAAB rec derivation
overview: CANCELLED — automated dbt cross-league bid derivation was superseded. Cross-league FTN guidance is in-app only (Streamlit expander in FAAB Worksheet). No `ftn_bid_scaling` seed and no `effective_high_bid` in the warehouse; user applies 1.3 / 1.4 / 1.25 / 1.5 heuristics mentally. See `apps/in-season-tool/app.py` and roadmap plan `faab_tighten`.
todos:
  - id: branch
    content: Create feat/1b-cross-league-faab-derivation branch off master
    status: cancelled
  - id: scaling_seed
    content: Add dbt/seeds/ftn_bid_scaling.csv with closer/rp/sp/default scale factors
    status: cancelled
  - id: mart_refactor
    content: "Refactor mart_faab_worksheet.sql: pivot FTN by league_size, join scaling seed, add effective_low_bid/effective_high_bid/bid_source/scale_applied; update high_bid_pct_of_faab to use effective_high_bid"
    status: cancelled
  - id: app_source_col
    content: "App: show effective bids, add Source column + 'Include derived recs' checkbox in sidebar"
    status: cancelled
  - id: validate
    content: dbt parse; smoke check Uribe in Cash 15 derives from 12-team with 1.5x closer factor
    status: cancelled
  - id: readme
    content: Add ftn_bid_scaling.csv row to README Manual data maintenance table
    status: cancelled
  - id: commit
    content: Commit and push feature branch for user review
    status: cancelled
isProject: false
---

# Superseded (2026-04-26)

**Decision:** Do not auto-scale missing FTN bids in dbt. The mart continues to show native low/high for each league’s `ftn_league_size` only.

**Replacement:** The in-season app’s FAAB Worksheet includes an expander **“Cross-league-size FTN recs (manual)”** in [`apps/in-season-tool/app.py`](apps/in-season-tool/app.py) with a markdown table of multipliers (12T↔15T) for mental translation. Shown for leagues with FAAB (hidden for draft-and-hold 50s).

**No warehouse changes:** no derived columns, no scaling seed, no change to `high_bid_pct_of_faab`.

---

## Original spec (archived, not implemented)

The content below was the prior design before cancellation.

## Branch

`feat/1b-cross-league-faab-derivation` off master.

## Data model changes

### 1. New seed: [`dbt/seeds/ftn_bid_scaling.csv`](dbt/seeds/ftn_bid_scaling.csv)

Tunable scaling factors, keyed on a case-insensitive substring match against FTN's `type` field. Evaluated in listed order; first match wins. `default` row is the catch-all.

```csv
type_pattern,scale_12_to_15,notes
closer,1.5,Saves are scarce; 15-team relievers get more aggressive bids
cl,1.5,Abbreviation variant used in some FTN exports
rp,1.4,Non-closer relievers with ratios/K upside
sp,1.25,Starters more plentiful than closers
default,1.3,Hitters and anything else uncategorized
```

`scale_15_to_12` is derived as `1/scale_12_to_15` in SQL — no need to list both.

### 2. Refactor [`dbt/models/main/mart_faab_worksheet.sql`](dbt/models/main/mart_faab_worksheet.sql)

Replace the `ftn_by_league` inner-join CTE with a pivot + scale pipeline:

- `ftn_pivoted`: one row per `nfbc_id` with columns `low_bid_12`, `high_bid_12`, `low_bid_15`, `high_bid_15`, plus shared metadata (`player_clean`, `ftn_type`, `ftn_notes`, `bid_change`, `status_tag`) picked with `coalesce(by_12, by_15)` so we don't lose notes that only appear in one side.
- `ftn_scaled`: cross-joined with `league_config` (filtered to non-null `ftn_league_size` only — preserves current nolen_50 behavior) and with `ftn_bid_scaling`, computing:
  - `effective_low_bid` / `effective_high_bid`: native value for this league size if present, else the other-size value × scaling factor (rounded to int)
  - `bid_source`: `'native'`, `'derived_from_12'`, or `'derived_from_15'`
  - `scale_applied`: numeric factor used (for debuggability)

Final select updates:
- Keep existing `low_bid` / `high_bid` as **native only** (unchanged semantics for any downstream consumer)
- Add `effective_low_bid`, `effective_high_bid`, `bid_source`, `scale_applied`
- Change `high_bid_pct_of_faab` to use `effective_high_bid` (so derived bids are % of budget too)
- Keep `has_ftn_rec` as "native rec present" (0/1); add `has_ftn_effective_rec` (0/1) for "native or derived"

Scaling formula example (Presto/Athena):

```sql
case
    when ftn.high_bid_15 is not null then ftn.high_bid_15
    when ftn.high_bid_12 is not null and lc.ftn_league_size = 15
        then cast(round(ftn.high_bid_12 * s.scale_12_to_15) as int)
    when ftn.high_bid_15 is not null and lc.ftn_league_size = 12
        then cast(round(ftn.high_bid_15 / s.scale_12_to_15) as int)
    ...
end as effective_high_bid
```

Type-based scaling lookup: pick the first `ftn_bid_scaling` row whose `type_pattern` appears (case-insensitive) in `ftn.ftn_type`, defaulting to the `default` row. Implemented with a ranked join or a simpler `case when lower(ftn_type) like '%closer%' then 1.5 ...` depending on which is cleaner in Athena.

## App changes — [`apps/in-season-tool/app.py`](apps/in-season-tool/app.py)

- Display `effective_high_bid` / `effective_low_bid` in the "High $" / "Low $" columns (rename nothing).
- Append a small `~` prefix or separate column badge when `bid_source != 'native'`, e.g. "~$180 (12T)".
- Add a new column `"Source"` in the FAAB dataframe that shows "native", "derived (12T)", or "derived (15T)" — users can sort/filter.
- Sidebar: add `"Include derived FTN recs"` checkbox, default `True`. When off, filters to `bid_source = 'native'`.
- `% of Budget` keeps reading `high_bid_pct_of_faab`, which is now based on effective bid.

## Tests / validation

- `dbt parse` locally (full build runs in dbt Cloud).
- Sanity-check with a known player in both files: `effective_high_bid == high_bid` and `bid_source == 'native'` for both league sizes.
- Sanity-check with Uribe (12-team only this week): `bid_source = 'derived_from_12'` for `nolen_cash_15`; `effective_high_bid ≈ round(160 * 1.5) = 240` given his `closer` type.
- Sanity-check 50s league: `bid_source is null` (no change from today).

## README update

Add `dbt/seeds/ftn_bid_scaling.csv` to the "Manual data maintenance" table with cadence "as-needed when factors feel off".

## Out of scope (keep it small)

- No competitor FAAB tracking (future Phase 1b iteration).
- No category-need weighting (Phase 1d, blocked on standings ingestion).
- No new "bid buckets" feature yet — that's a separate Phase 1b branch.
