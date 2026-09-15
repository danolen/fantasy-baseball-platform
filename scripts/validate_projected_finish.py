"""SELECT-only hand-calc for mart_projected_overall_finish_category (#188).

dbt unit tests on the agent target require Glue DDL that this IAM user
cannot perform. This runs the same mocked inputs as a plain Athena SELECT
and checks the focused expect columns.
"""

from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boto3

from gen_projected_finish_unit_test import EXPECT, REFS, given_sqls

WORKGROUP = os.environ["ATHENA_WORKGROUP"]
OUTPUT = os.environ["ATHENA_S3_OUTPUT"]
REGION = os.environ["ATHENA_REGION"]

MODEL_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "dbt",
    "models",
    "main",
    "mart_projected_overall_finish_category.sql",
)

FOCUS = [
    "scenario",
    "category",
    "remaining_core_raw",
    "remaining_replacement_raw",
    "projected_final",
    "projected_category_points",
    "n_core_starters_hitters",
    "n_repl_hitters",
    "projected_overall_points",
    "projected_overall_rank",
    "output_kind",
]


def render_model(sql: str) -> str:
    sql = sql[sql.index("}}") + 2 :]
    sql = re.sub(r"\{%\s*set\s+hist_oc\s*=\s*\"([^\"]+)\"\s*%\}", "", sql)
    sql = re.sub(r"\{%\s*set\s+hist_50s\s*=\s*\"([^\"]+)\"\s*%\}", "", sql)
    sql = sql.replace("{{ hist_oc }}", "NFBC OC 2025 Overall Standings.csv")
    sql = sql.replace("{{ hist_50s }}", "NFBC 50s 2025 Overall Standings.csv")
    for name in REFS:
        sql = sql.replace("{{ ref('%s') }}" % name, "mock_%s" % name)
    if "{{" in sql or "{%" in sql:
        raise RuntimeError("unrendered Jinja remains")
    return sql


def build_query(model_sql: str) -> str:
    mocks = ",\n".join(
        f"mock_{name} AS ({sql})" for name, sql in given_sqls().items()
    )
    rendered = render_model(model_sql)
    replaced, n = re.subn(
        r"^\s*with\s",
        "WITH " + mocks + ",\n",
        rendered,
        count=1,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if n != 1:
        raise RuntimeError("could not anchor mock CTEs")
    focus = ", ".join(FOCUS)
    return f"SELECT {focus} FROM (\n{replaced}\n) actual"


def run_athena(sql: str):
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


def to_py(col: str, raw):
    if raw is None:
        return None
    if col in ("scenario", "category", "output_kind"):
        return raw
    if col in ("n_core_starters_hitters", "n_repl_hitters", "projected_overall_rank"):
        return int(float(raw))
    return float(raw)


def close(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    return abs(float(a) - float(b)) < 1e-9


def main() -> int:
    with open(MODEL_PATH, encoding="utf-8") as handle:
        model_sql = handle.read()
    sql = build_query(model_sql)
    header, data = run_athena(sql)
    got = {
        (r[header.index("scenario")], r[header.index("category")]): r
        for r in data
    }
    print(f"rows returned: {len(data)}")
    fails = 0
    for exp in EXPECT:
        key = (exp["scenario"], exp["category"])
        if key not in got:
            fails += 1
            print(f"MISSING {key}")
            continue
        row = got[key]
        for col in FOCUS:
            actual = to_py(col, row[header.index(col)])
            want = exp[col]
            if not close(actual, want):
                fails += 1
                print(f"MISMATCH {key} {col}: got {actual!r} want {want!r}")
    extra = set(got) - {(e["scenario"], e["category"]) for e in EXPECT}
    for key in sorted(extra):
        fails += 1
        print(f"EXTRA {key}")
    print(f"assertions on {len(EXPECT)} rows, {fails} failures")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
