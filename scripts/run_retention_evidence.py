"""Run #185 evidence models as read-only Athena SELECTs (no Glue DDL).

Renders each model SQL with refs swapped to prod tables, executes, and
prints the results used to justify the assumptions seed.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boto3

from validate_ros_sgp_calibration import run_athena

STAGE = "dbt_stage"
MAIN = "dbt_main"
SOURCE = "dbt_source"


def find_table(name):
    g = boto3.client("glue", region_name=os.environ["ATHENA_REGION"])
    for db in (SOURCE, MAIN, STAGE, "dbt"):
        try:
            g.get_table(DatabaseName=db, Name=name)
            return f"{db}.{name}"
        except Exception:
            continue
    raise RuntimeError(f"table not found: {name}")


LEAGUE_CONFIG = find_table("league_config")

REFS = {
    "{{ ref('src_nfbc_in_season_players_history') }}": f"{SOURCE}.src_nfbc_in_season_players_history",
    "{{ ref('league_config') }}": LEAGUE_CONFIG,
    "{{ ref('stg_nfbc_roster_membership') }}": "membership",
    "{{ ref('stg_proj_rest_of_season_hitting_values_oc') }}": f"{STAGE}.stg_proj_rest_of_season_hitting_values_oc",
    "{{ ref('stg_proj_rest_of_season_pitching_values_oc') }}": f"{STAGE}.stg_proj_rest_of_season_pitching_values_oc",
    "{{ ref('stg_proj_rest_of_season_hitting_values_me') }}": f"{STAGE}.stg_proj_rest_of_season_hitting_values_me",
    "{{ ref('stg_proj_rest_of_season_pitching_values_me') }}": f"{STAGE}.stg_proj_rest_of_season_pitching_values_me",
    "{{ ref('mart_weekly_lineup_inputs') }}": f"{MAIN}.mart_weekly_lineup_inputs",
}


def load_model(path):
    with open(path) as f:
        sql = f.read()
    return sql[sql.index("}}") + 2:]


def strip_membership_cte(sql):
    """Drop the evidence models' own `membership` CTE (we prepend live SQL).

    Finds `membership as (` after the leading WITH and removes through the
    matching close paren plus trailing comma, leaving the remaining CTEs.
    """
    m = re.search(r"(?i)\bmembership\s+as\s*\(", sql)
    assert m, "membership CTE not found"
    depth = 0
    for i in range(m.end() - 1, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                rest = sql[i + 1:].lstrip()
                assert rest.startswith(","), "expected comma after membership CTE"
                # Drop the leading WITH as well: caller prepends its own.
                # Allow SQL comments/whitespace before it.
                head = re.sub(r"(?i)^\s*with\s+", "",
                              re.sub(r"(?m)^\s*--[^\n]*\n", "", sql[:m.start()]))
                assert head.strip() == "", f"unexpected text before membership CTE: {head[:80]!r}"
                return rest[1:].lstrip()
    raise AssertionError("unbalanced parens in membership CTE")


def render(path):
    sql = load_model(path)
    for k, v in REFS.items():
        sql = sql.replace(k, v)
    assert "{{" not in sql, f"unrendered Jinja in {path}"
    return sql


def membership_with_format(membership_sql, lc_table):
    # Mirrors the stripped membership CTEs (configured oc/me leagues only).
    return (f"SELECT s.*, lc.format AS format FROM ({membership_sql}) s "
            f"INNER JOIN {lc_table} lc ON lc.league = s.league "
            f"WHERE lc.format IN ('oc', 'me')")


def show(title, header, rows, limit=60):
    print(f"===== {title} ({len(rows)} rows) =====")
    print(" | ".join(header))
    for r in rows[:limit]:
        print(" | ".join("" if v is None else str(v) for v in r))
    print()


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    membership_sql = render("dbt/models/stage/nfbc/stg_nfbc_roster_membership.sql")
    if which in ("all", "membership"):
        h, d = run_athena(
            f"WITH membership AS ({membership_sql}) "
            "SELECT format, row_type, count(*) n, count(DISTINCT league) leagues, "
            "count(DISTINCT week_key) weeks, min(week_key) first_wk, max(week_key) last_wk "
            "FROM membership m LEFT JOIN "
            f"(SELECT league, format FROM {LEAGUE_CONFIG}) lc USING (league) "
            "GROUP BY 1, 2 ORDER BY 1, 2")
        # membership has no format column; resolve via join above
        show("membership by format/type", h, d)
    if which in ("all", "retention"):
        # The evidence model refs membership by name; CTE already defined.
        body = strip_membership_cte(
            render("dbt/models/main/mart_retained_core_evidence.sql"))
        h, d = run_athena(
            f"WITH membership AS ({membership_with_format(membership_sql, LEAGUE_CONFIG)}), "
            + body + " ORDER BY 1, 2, 3, 5")
        show("retention evidence", h, d, limit=80)
    if which in ("all", "churn"):
        body = strip_membership_cte(
            render("dbt/models/main/mart_streaming_churn_evidence.sql"))
        h, d = run_athena(
            f"WITH membership AS ({membership_with_format(membership_sql, LEAGUE_CONFIG)}), "
            + body)
        show("churn evidence", h, d)
    if which in ("all", "fa"):
        h, d = run_athena(render("dbt/models/main/mart_fa_replacement_curve.sql")
                          + " ORDER BY 1, 2, 3")
        show("FA replacement curve", h, d, limit=70)


if __name__ == "__main__":
    main()
