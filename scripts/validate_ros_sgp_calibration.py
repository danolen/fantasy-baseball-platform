"""Agent-target validation for mart_ros_sgp_calibration without Glue DDL.

Replicates `dbt test` for the two calibration unit tests by running the
model's own SQL with mocked refs as inline CTEs through Athena as plain
SELECTs (no table creation), then asserting the same focused columns the
dbt unit tests assert. Exits nonzero on any mismatch.
"""

from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import boto3

from gen_ros_sgp_unit_tests import (
    CATS,
    HIST,
    HIST_FILE,
    STAGED_COLS,
    expected_rows,
    league_rows,
)

WORKGROUP = os.environ["ATHENA_WORKGROUP"]
OUTPUT = os.environ["ATHENA_S3_OUTPUT"]
REGION = os.environ["ATHENA_REGION"]

FOCUS = ["format", "category", "hist_slope", "current_slope_raw",
         "current_slope", "season_completion", "current_weight",
         "final_slope", "hist_ctx_num", "hist_ctx_den", "hist_ctx_rate",
         "final_ctx_num", "final_ctx_den", "final_ctx_rate", "is_fallback"]


def mock_calendar_sql(periods, start):
    return (f"SELECT 2026 AS season_year, {periods} AS scoring_periods, "
            f"DATE '{start}' AS season_start_date")


def mock_factors_sql():
    lines = []
    for fmt in ("oc", "me", "50s"):
        h = HIST[fmt]
        cols = ", ".join(f"CAST({h[c]!r} AS DOUBLE) AS sgp_{c.lower()}" for c in CATS)
        lines.append(f"SELECT '{HIST_FILE[fmt]}' AS _filename, {cols}")
    return "\nUNION ALL ".join(lines)


def mock_staged_sql(rows):
    lines = []
    for r in rows:
        vals = []
        for c in STAGED_COLS:
            v = r[c]
            if c in ("format", "owner", "team"):
                vals.append(f"'{v}' AS {c}")
            elif c == "snapshot_date":
                vals.append(f"DATE '{v}' AS {c}")
            elif c == "is_latest_snapshot":
                vals.append(f"{'TRUE' if v else 'FALSE'} AS {c}")
            elif c in ("nfbc_league_id", "standing_rank"):
                vals.append(f"{int(v)} AS {c}")
            else:
                vals.append(f"CAST({float(v)!r} AS DOUBLE) AS {c}")
        lines.append("SELECT " + ", ".join(vals))
    return "\nUNION ALL ".join(lines)


def build_query(model_sql, calendar, staged):
    q = model_sql
    q = q.replace("{{ ref('season_scoring_calendar') }}", "mock_calendar")
    q = q.replace("{{ ref('mart_sgp_factors') }}", "mock_factors")
    q = q.replace("{{ ref('stg_nfbc_in_season_overall_category_stats') }}", "mock_staged")
    assert "{{" not in q and "{%" not in q, "unrendered Jinja remains"
    mocks = (f"mock_calendar AS ({mock_calendar_sql(*calendar)}),\n"
             f"mock_factors AS ({mock_factors_sql()}),\n"
             f"mock_staged AS ({mock_staged_sql(staged)}),\n")
    q2, n = re.subn(r"^\s*with\s", "WITH " + mocks, q, count=1,
                    flags=re.IGNORECASE | re.MULTILINE)
    assert n == 1, "could not anchor mock CTEs"
    return q2


def run_athena(sql):
    client = boto3.client("athena", region_name=REGION)
    qid = client.start_query_execution(
        QueryString=sql,
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": OUTPUT},
    )["QueryExecutionId"]
    while True:
        st = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if st["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(3)
    if st["State"] != "SUCCEEDED":
        raise RuntimeError(st.get("StateChangeReason", st["State"]))
    paginator = client.get_paginator("get_query_results")
    rows = []
    for page in paginator.paginate(QueryExecutionId=qid):
        rows.extend(page["ResultSet"]["Rows"])
    header = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
    return header, [[c.get("VarCharValue") for c in r["Data"]] for r in rows[1:]]


def to_py(col, raw):
    if raw is None:
        return None
    if col in ("is_ratio", "is_fallback"):
        return raw.lower() == "true"
    if col in ("format", "category", "hist_source_file"):
        return raw
    if col in ("scoring_periods_elapsed", "season_scoring_periods"):
        return int(float(raw))
    if col == "snapshot_date":
        return raw[:10]
    return float(raw)


def check(name, calendar, staged, exp):
    with open("dbt/models/main/mart_ros_sgp_calibration.sql") as f:
        model_sql = f.read()
    # Strip the {{ config() }} block (first }} occurrence).
    model_sql = model_sql[model_sql.index("}}") + 2:]
    sql = build_query(model_sql, calendar, staged)
    header, data = run_athena(sql)
    got = {(r[header.index("format")], r[header.index("category")]): r for r in data}
    assert len(got) == 30, f"{name}: expected 30 rows, got {len(got)}"
    fails = 0
    for key, e in sorted(exp.items()):
        row = got[key]
        for col in FOCUS:
            actual = to_py(col, row[header.index(col)])
            want = e[col]
            ok = (actual == want) or (
                isinstance(want, float) and isinstance(actual, float)
                and abs(actual - want) == 0.0)
            if not ok:
                fails += 1
                print(f"MISMATCH {name} {key} {col}: got {actual!r} want {want!r}")
    print(f"{name}: {30 * len(FOCUS)} assertions, {fails} failures")
    return fails


def main():
    total = 0
    oc_rows = league_rows("oc", 11, "2026-04-10")
    exp1 = expected_rows({"oc": (oc_rows, "2026-04-10", 3)}, 4, None)
    total += check("early_blend", (4, "2026-03-27"), oc_rows, exp1)
    s50_rows = league_rows("50s", 12, "2026-10-02")
    exp2 = expected_rows({"50s": (s50_rows, "2026-10-02", 27)}, 27, None)
    total += check("late_season", (27, "2026-03-27"), s50_rows, exp2)
    print("TOTAL FAILURES:", total)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
