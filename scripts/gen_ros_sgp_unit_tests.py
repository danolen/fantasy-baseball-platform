"""Generate _unit_tests_ros_sgp_calibration.yml with bit-exact expectations.

Mock data uses integer- or dyadic-linear stats so every OLS slope, average,
and blend step is a deterministic IEEE-754 result reproducible by hand here
and by Athena, in any aggregation order:

* counting cats: integer-linear in standings points (exact slope),
  or integer-constant (exact 0.0 slope);
* ratio cats: dyadic-linear (AVG, exact 1/128 slope) or dyadic-constant
  (ERA 3.5, WHIP 1.25, exact 0.0 slope);
* ratio contexts: integer-constant H/AB/ER/IP/HA/BB (exact averages).

Expected values below are computed from the emitted mock rows with the same
operation order as mart_ros_sgp_calibration.sql.
"""

from __future__ import annotations

import io

OUT = "dbt/models/main/_unit_tests_ros_sgp_calibration.yml"

CATS = ["R", "HR", "RBI", "SB", "AVG", "K", "W", "S", "ERA", "WHIP"]
COUNTING = {"R", "HR", "RBI", "SB", "K", "W", "S"}

# Mock historical slopes per file (chosen for exact downstream math).
HIST = {
    "oc": {"R": 21.0, "HR": 9.0, "RBI": 21.0, "SB": 12.0, "AVG": 0.002,
            "K": 30.0, "W": 12.0, "S": 15.0, "ERA": -0.08, "WHIP": -0.04},
    "me": {"R": 19.0, "HR": 8.0, "RBI": 19.0, "SB": 11.0, "AVG": 0.0018,
            "K": 28.0, "W": 11.0, "S": 13.0, "ERA": -0.07, "WHIP": -0.036},
    "50s": {"R": 22.0, "HR": 9.5, "RBI": 22.5, "SB": 12.5, "AVG": 0.0021,
             "K": 31.0, "W": 12.5, "S": 15.5, "ERA": -0.084, "WHIP": -0.044},
}
HIST_FILE = {
    "oc": "NFBC OC 2025 Overall Standings.csv",
    "me": "NFBC ME 2025 Overall Standings.csv",
    "50s": "NFBC 50s 2025 Overall Standings.csv",
}
# Historical ratio contexts (transcribed from the pre-#184 models).
HIST_CTX = {
    "oc": {"AVG": (1765.0, 6958.0, 0.2536), "ERA": (487.0, 1163.0, 3.7707),
            "WHIP": (1398.0, 1163.0, 1.2022)},
    "me": {"AVG": (1712.0, 6803.0, 0.2517), "ERA": (499.0, 1155.0, 3.885),
            "WHIP": (1415.0, 1155.0, 1.223)},
    "50s": {"AVG": (1725.0, 6805.0, 0.2535), "ERA": (474.0, 1131.0, 3.774),
             "WHIP": (1359.0, 1131.0, 1.201)},
}

STAGED_COLS = ["format", "nfbc_league_id", "owner", "team", "standing_rank",
               "r", "hr", "rbi", "sb", "avg", "k", "w", "sv", "era", "whip",
               "h", "ab", "er", "ip", "ha", "bb", "snapshot_date",
               "is_latest_snapshot"]


def league_rows(fmt, league_id, snapshot, slope_r=9.0, base_r=100.0,
                 avg_b=1.0 / 128.0, avg_base=1.0):
    """12 teams; R integer-linear (slope slope_r), AVG dyadic-linear.

    Points p = 13 - rank. Mid-table filter keeps ranks 3..10 either way.
    """
    rows = []
    for rank in range(1, 13):
        p = 13 - rank
        rows.append({
            "format": fmt, "nfbc_league_id": league_id,
            "owner": f"o{rank}", "team": f"t{rank}", "standing_rank": rank,
            "r": base_r + slope_r * p,
            "hr": 200.0, "rbi": 900.0, "sb": 100.0,
            "avg": avg_base + avg_b * p,
            "k": 1200.0, "w": 80.0, "sv": 60.0,
            "era": 3.5, "whip": 1.25,
            "h": 1400.0, "ab": 5600.0, "er": 450.0, "ip": 1125.0,
            "ha": 100.0, "bb": 50.0,
            "snapshot_date": snapshot, "is_latest_snapshot": True,
        })
    return rows


def ols(xs, ys):
    n = len(xs)
    sx, sy, sxy, sx2 = sum(xs), sum(ys), sum(x * y for x, y in zip(xs, ys)), sum(x * x for x in xs)
    den = n * sx2 - sx * sx
    return (n * sxy - sx * sy) / den if den else None


VAL_COL = {"S": "sv"}


def league_slope(rows, cat):
    """Replicate the mart's rank/points/OLS for one league's rows."""
    vcol = VAL_COL.get(cat, cat.lower())
    if cat in ("ERA", "WHIP"):
        ordered = sorted(rows, key=lambda r: r[vcol])
    else:
        ordered = sorted(rows, key=lambda r: -r[vcol])
    pts, vals = [], []
    for i, r in enumerate(ordered, start=1):
        if 3 <= i <= 10:
            pts.append(13 - i)
            vals.append(float(r[vcol]))
    return ols(pts, vals)


def expected_rows(formats_rows, periods_total, start_weeks_elapsed):
    """formats_rows: {fmt: (rows, snapshot_iso, elapsed)}.

    Returns { (fmt, cat): expected dict } for all 3 formats x 10 cats.
    """
    exp = {}
    for fmt in ("oc", "me", "50s"):
        rows, snap, elapsed = formats_rows.get(fmt, (None, None, None))
        if rows is None:
            w, completion = 0.0, 0.0
            snap_out = None
        else:
            completion = elapsed * 1.0 / periods_total
            w = min(max(completion, 0.0), 1.0)
            snap_out = snap
        for cat in CATS:
            ratio = cat not in COUNTING
            hist = HIST[fmt][cat]
            if rows is None:
                raw = None
                cur = None
            else:
                raw = league_slope(rows, cat)
                cur = raw if ratio else raw / completion
            if cur is None or hist is None:
                final = hist if hist is not None else cur
            else:
                final = w * cur + (1.0 - w) * hist
            if ratio:
                hnum, hden, hrate = HIST_CTX[fmt][cat]
                if rows is None:
                    cnum = cden = crate = None
                    fnum, fden, frate = hnum, hden, hrate
                else:
                    if cat == "AVG":
                        ah = sum(r["h"] for r in rows) / len(rows)
                        ab = sum(r["ab"] for r in rows) / len(rows)
                        cnum, cden = ah * 13.0 / 14.0, ab * 13.0 / 14.0
                        crate = sum(r["h"] for r in rows) / sum(r["ab"] for r in rows)
                    elif cat == "ERA":
                        ae = sum(r["er"] for r in rows) / len(rows)
                        ai = sum(r["ip"] for r in rows) / len(rows)
                        cnum, cden = ae * 8.0 / 9.0, ai * 8.0 / 9.0
                        crate = 9.0 * sum(r["er"] for r in rows) / sum(r["ip"] for r in rows)
                    else:
                        an = sum(r["ha"] + r["bb"] for r in rows) / len(rows)
                        ai = sum(r["ip"] for r in rows) / len(rows)
                        cnum, cden = an * 8.0 / 9.0, ai * 8.0 / 9.0
                        crate = sum(r["ha"] + r["bb"] for r in rows) / sum(r["ip"] for r in rows)
                    fnum = w * cnum + (1.0 - w) * hnum
                    fden = w * cden + (1.0 - w) * hden
                    frate = w * crate + (1.0 - w) * hrate
            else:
                hnum = hden = hrate = cnum = cden = crate = None
                fnum = fden = frate = None
            exp[(fmt, cat)] = {
                "format": fmt, "category": cat, "is_ratio": ratio,
                "hist_source_file": HIST_FILE[fmt], "hist_slope": hist,
                "current_slope_raw": raw, "current_slope": cur,
                "snapshot_date": snap_out,
                "scoring_periods_elapsed": elapsed if elapsed is not None else 1,
                "season_scoring_periods": periods_total,
                "season_completion": completion,
                "current_weight": w, "hist_weight": 1.0 - w,
                "final_slope": final,
                "hist_ctx_num": hnum, "hist_ctx_den": hden, "hist_ctx_rate": hrate,
                "current_ctx_num": cnum, "current_ctx_den": cden,
                "current_ctx_rate": crate,
                "final_ctx_num": fnum, "final_ctx_den": fden,
                "final_ctx_rate": frate,
                "is_fallback": cur is None,
            }
    return exp


def staged_given(rows):
    lines = []
    for r in rows:
        vals = []
        for c in STAGED_COLS:
            v = r[c]
            if c in ("format", "owner", "team"):
                vals.append(f"'{v}' as {c}")
            elif c == "snapshot_date":
                vals.append(f"date '{v}' as {c}")
            elif c == "is_latest_snapshot":
                vals.append(f"{'true' if v else 'false'} as {c}")
            elif c in ("nfbc_league_id", "standing_rank"):
                vals.append(f"{int(v)} as {c}")
            else:
                vals.append(f"cast({float(v)!r} as double) as {c}")
        lines.append("select " + ", ".join(vals))
    return "\n          union all ".join(lines)


def fmt_val(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return f"'{v}'"


def build_test(name, description, calendar_row, staged_rows, exp):
    buf = io.StringIO()
    buf.write(f"  - name: {name}\n")
    buf.write(f"    description: >-\n      {description}\n")
    buf.write("    model: mart_ros_sgp_calibration\n")
    buf.write("    given:\n")
    buf.write("      - input: ref('season_scoring_calendar')\n")
    buf.write("        format: sql\n")
    buf.write("        rows: |\n")
    buf.write(
        f"          select 2026 as season_year, "
        f"{calendar_row[0]} as scoring_periods, "
        f"date '{calendar_row[1]}' as season_start_date\n"
    )
    buf.write("      - input: ref('mart_sgp_factors')\n")
    buf.write("        format: sql\n")
    buf.write("        rows: |\n")
    fac_lines = []
    for fmt in ("oc", "me", "50s"):
        h = HIST[fmt]
        fac_lines.append(
            f"          select '{HIST_FILE[fmt]}' as _filename, "
            + ", ".join(f"cast({h[c]!r} as double) as sgp_{c.lower()}" for c in CATS)
        )
    buf.write("\n          union all ".join(fac_lines) + "\n")
    buf.write("      - input: ref('stg_nfbc_in_season_overall_category_stats')\n")
    buf.write("        format: sql\n")
    buf.write("        rows: |\n")
    buf.write("          " + staged_given(staged_rows).replace("\n", "\n          ") + "\n")
    buf.write("    expect:\n")
    buf.write("      rows:\n")
    # Every expected row must carry the IDENTICAL column set: dbt renders
    # each row as a union-all branch with only its own keys, so a missing
    # key on any row fails with "union has different number of fields".
    cols = ["format", "category", "hist_slope", "current_slope_raw",
            "current_slope", "season_completion", "current_weight",
            "final_slope", "hist_ctx_num", "hist_ctx_den", "hist_ctx_rate",
            "final_ctx_num", "final_ctx_den", "final_ctx_rate", "is_fallback"]
    seen_keysets = set()
    for fmt in ("oc", "me", "50s"):
        for cat in CATS:
            e = exp[(fmt, cat)]
            parts = []
            for k in cols:
                v = e[k]
                parts.append(f"{k}: {fmt_val(v)}")
            buf.write("        - {" + ", ".join(parts) + "}\n")
            seen_keysets.add(tuple(cols))
    assert len(seen_keysets) == 1
    return buf.getvalue()


def main():
    header = """version: 2

# ROS SGP calibration unit tests (#184). Mock data is integer-/dyadic-linear
# so every OLS slope and blend step is bit-exact (see scripts/gen script).
# Covers: counting annualization, ratio context retention (no annualization),
# early vs late blend weights, and the ME historical fallback.

unit_tests:
"""
    # Test 1: early season (3 of 4 mock periods -> w = 0.75), oc only.
    oc_rows = league_rows("oc", 11, "2026-04-10")
    exp1 = expected_rows({"oc": (oc_rows, "2026-04-10", 3)}, 4, None)
    t1 = build_test(
        "test_ros_sgp_early_blend_annualizes_counting",
        "Early season (w=0.75): counting slopes annualize (9 -> 12) then "
        "blend with history (R -> 14.25); AVG keeps its raw slope and blends "
        "contexts; ME has no current leg and falls back to history.",
        (4, "2026-03-27"),
        oc_rows,
        exp1,
    )
    # Test 2: late season (27 of 27 -> w = 1.0), 50s only.
    s50_rows = league_rows("50s", 12, "2026-10-02")
    exp2 = expected_rows({"50s": (s50_rows, "2026-10-02", 27)}, 27, None)
    t2 = build_test(
        "test_ros_sgp_late_season_uses_current_leg",
        "Late season (w=1.0): 50s finals equal annualized current slopes "
        "and current contexts; ME and oc fall back to history.",
        (27, "2026-03-27"),
        s50_rows,
        exp2,
    )
    with open(OUT, "w") as f:
        f.write(header + t1 + t2)
    print(f"wrote {OUT}")
    # Sanity print of the headline assertions.
    for key in [("oc", "R"), ("oc", "AVG"), ("oc", "ERA"), ("me", "R")]:
        e = exp1[key]
        print(key, "raw=", e["current_slope_raw"], "cur=", e["current_slope"],
              "final=", e["final_slope"], "w=", e["current_weight"],
              "fallback=", e["is_fallback"])
    for key in [("50s", "R"), ("50s", "AVG"), ("me", "R"), ("oc", "R")]:
        e = exp2[key]
        print(key, "raw=", e["current_slope_raw"], "cur=", e["current_slope"],
              "final=", e["final_slope"], "w=", e["current_weight"],
              "fallback=", e["is_fallback"])


if __name__ == "__main__":
    main()
