---
name: dbt mart
about: New or changed mart model for Athena / downstream apps
title: "[dbt mart] "
---

## Mart name & location

<!-- e.g. `models/main/mart_foo.sql` -->

## Grain & primary key

<!-- One row per …; key column(s). -->

## Outcome

<!-- What consumers get (columns, semantics, freshness). -->

## Upstream dependencies

<!-- Major `ref()` / `source()` models or tables. -->

## Downstream impact

<!-- Draft tool, in-season tool, exports, external consumers. -->

## Tests & validation

<!-- `dbt parse`, schema tests, unit tests, or sample query expectations. -->

## Acceptance criteria

- [ ] Model builds in target environment
- [ ] Tests / docs updated as needed
- [ ] Apps or docs referencing the mart updated (if applicable)
