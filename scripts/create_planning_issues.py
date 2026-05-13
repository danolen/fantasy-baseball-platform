#!/usr/bin/env python3
"""
Create the planning issues defined in ``scripts/issues.py`` on GitHub.

Usage
-----

    # Preview the plan without touching GitHub
    python scripts/create_planning_issues.py --dry-run

    # Create everything (uses GH_PAT or whatever `gh auth` is set up with)
    python scripts/create_planning_issues.py

    # Create everything but skip label creation/assignment
    python scripts/create_planning_issues.py --skip-labels

Authentication and permissions
------------------------------
The script shells out to the ``gh`` CLI. If ``GH_PAT`` is set it is used
as the token; otherwise the script falls back to whatever ``gh auth``
has configured. The token needs:

- ``Issues: read and write`` — to create and label issues.
- ``Metadata: read`` — to read repo metadata.

The Cursor Cloud Agent's default token can *create* issues but cannot
create labels, assign labels, edit issues, comment, or close issues. To
use this script with full label support, generate a fine-grained PAT
scoped to the repo with the permissions above and either:

1. Add it as a Secret named ``GH_PAT`` in the Cursor dashboard
   (Cloud Agents → Secrets). It will be injected on the next agent run.
2. Or run the script locally with ``GH_PAT=ghp_xxx python ...``.

If neither is possible, run with ``--skip-labels`` to create the issues
unlabeled. You can label them later by hand or by re-running this
script with a properly-scoped token (it skips already-created issues).

State
-----
State is persisted to ``scripts/.issue_state.json`` so re-runs are
idempotent. Delete that file to start over (or to retarget another
repo).

Creation order
--------------
The script creates issues bottom-up:

1. Leaves first (so we can embed leaf numbers in epic bodies).
2. Epics with the child checklist already in the body.
3. The roadmap last with the epic checklist already in the body.

This works around the constraint that some tokens cannot edit issues
after creation; nothing is ever updated, only created.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from issues import REPO, get_issues  # type: ignore  # noqa: E402

STATE_PATH = HERE / ".issue_state.json"


def load_state() -> dict[str, int]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict[str, int]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def gh(*args: str, stdin: str | None = None, check: bool = True) -> tuple[int, str, str]:
    env = os.environ.copy()
    pat = env.get("GH_PAT")
    if pat:
        env["GH_TOKEN"] = pat
    result = subprocess.run(
        ["gh", *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed (rc={result.returncode}):\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    return result.returncode, result.stdout, result.stderr


def issue_number_from_url(url: str) -> int:
    m = re.search(r"/issues/(\d+)", url.strip())
    if not m:
        raise RuntimeError(f"Could not parse issue number from URL: {url!r}")
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Label management
# ---------------------------------------------------------------------------

LABEL_COLORS: dict[str, tuple[str, str]] = {
    "phase:1-automation": ("1f6feb", "Phase 1: data automation work"),
    "phase:2-app": ("5319e7", "Phase 2: in-season app improvements"),
    "phase:3-ai": ("b45ee5", "Phase 3: AI engineering"),
    "area:automation": ("0e8a16", "Ingestion / scheduling / orchestration"),
    "area:dbt": ("006b75", "dbt models, seeds, tests, sources"),
    "area:in-season-tool": ("fbca04", "apps/in-season-tool changes"),
    "area:draft-tool": ("f9d0c4", "apps/draft-tool changes"),
    "area:platform": ("1d76db", "CI, infra, repo hygiene, observability"),
    "area:ai": ("d93f0b", "LLMs, embeddings, RAG, agents"),
    "epic": ("bfdadc", "Tracking issue grouping related work"),
    "book:in-season": ("c5def5", "Concept from The Process: In-Season Management"),
    "book:faab": ("c5def5", "Concept from The Process: FAAB strategy"),
    "book:lineup": ("c5def5", "Concept from The Process: Lineup Setting"),
    "quick-win": ("fef2c0", "Small, high-value, do-first"),
    "chore": ("cccccc", "Maintenance / hygiene / docs"),
    "nice-to-have": ("ededed", "Low priority; not blocking"),
}


def ensure_labels(*, skip: bool) -> set[str]:
    """Create any missing custom labels. Return the set of labels that
    exist on the repo after this call (so callers can filter unsupported
    labels out of per-issue label lists).
    """
    rc, out, _ = gh("label", "list", "--repo", REPO, "--limit", "200", check=False)
    existing: set[str] = set()
    if rc == 0:
        for line in out.splitlines():
            if not line.strip():
                continue
            existing.add(line.split("\t", 1)[0])

    if skip:
        return existing

    failures: list[str] = []
    for name, (color, desc) in LABEL_COLORS.items():
        if name in existing:
            continue
        rc, _out, err = gh(
            "label",
            "create",
            name,
            "--repo",
            REPO,
            "--color",
            color,
            "--description",
            desc,
            check=False,
        )
        if rc == 0:
            existing.add(name)
            print(f"  + label {name}")
        else:
            failures.append(name)

    if failures:
        print(
            "\nWARNING: could not create these labels (token likely lacks "
            "permission):\n  " + "\n  ".join(failures)
        )
        print(
            "Issues will still be created, but without these labels. Add a\n"
            "fine-grained PAT (Issues: read/write) as GH_PAT and re-run.\n"
        )
    return existing


# ---------------------------------------------------------------------------
# Body assembly
# ---------------------------------------------------------------------------

def assemble_body(issue: dict, *, state: dict[str, int], all_issues: list[dict]) -> str:
    kind = issue["kind"]
    if kind == "leaf":
        return issue["body"]

    base = issue["body_intro"]
    if kind == "roadmap":
        children = [i for i in all_issues if i["kind"] == "epic"]
    elif kind == "epic":
        children = [
            i
            for i in all_issues
            if i["kind"] == "leaf" and i.get("parent_key") == issue["key"]
        ]
    else:
        return base

    if not children:
        return base

    lines = []
    for child in children:
        number = state.get(child["key"])
        if number:
            lines.append(f"- [ ] #{number} — {child['title']}")
        else:
            lines.append(f"- [ ] _(pending {child['key']}: {child['title']})_")
    checklist = "\n".join(lines)

    placeholder = "(auto-populated once children are created)"
    if placeholder in base:
        return base.replace(placeholder, "\n" + checklist + "\n")
    return base + "\n\n" + checklist + "\n"


# ---------------------------------------------------------------------------
# Issue creation
# ---------------------------------------------------------------------------

def create_issue(
    *,
    title: str,
    body: str,
    labels: list[str],
    available_labels: set[str],
) -> int:
    args = ["issue", "create", "--repo", REPO, "--title", title, "--body-file", "-"]
    applied = [l for l in labels if l in available_labels]
    for label in applied:
        args += ["--label", label]
    _, out, _ = gh(*args, stdin=body)
    return issue_number_from_url(out.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print plan only.")
    parser.add_argument(
        "--skip-labels",
        action="store_true",
        help="Do not attempt to create or assign custom labels.",
    )
    parser.add_argument(
        "--apply-labels-only",
        action="store_true",
        help=(
            "Skip creation. For every issue in the state file, ensure the "
            "labels in scripts/issues.py are present on the issue. Use this "
            "after granting a PAT to retro-label previously-created issues."
        ),
    )
    args = parser.parse_args(argv)

    issues = get_issues()
    state = load_state()

    if args.dry_run:
        for issue in issues:
            tag = (
                "ROAD "
                if issue["kind"] == "roadmap"
                else ("EPIC " if issue["kind"] == "epic" else "leaf ")
            )
            present = "✓" if issue["key"] in state else "·"
            print(f"  {present} {tag} {issue['key']:>8}  {issue['title']}")
        print(
            f"\n{sum(1 for i in issues if i['key'] in state)} of "
            f"{len(issues)} already created."
        )
        return 0

    print(f"Target repo: {REPO}")
    available_labels = ensure_labels(skip=args.skip_labels)

    if args.apply_labels_only:
        for issue in issues:
            number = state.get(issue["key"])
            if not number:
                continue
            applied = [l for l in issue["labels"] if l in available_labels]
            if not applied:
                continue
            edit_args = ["issue", "edit", str(number), "--repo", REPO]
            for label in applied:
                edit_args += ["--add-label", label]
            rc, _out, err = gh(*edit_args, check=False)
            if rc == 0:
                print(f"  labelled {issue['key']:>8} #{number}: {applied}")
            else:
                print(f"  FAILED {issue['key']:>8} #{number}: {err.strip()}")
        return 0

    # 1) Create leaves first (so their numbers are known when we build
    #    each epic's checklist).
    for issue in issues:
        if issue["kind"] != "leaf":
            continue
        if issue["key"] in state:
            continue
        body = assemble_body(issue, state=state, all_issues=issues)
        number = create_issue(
            title=issue["title"],
            body=body,
            labels=issue["labels"],
            available_labels=available_labels,
        )
        state[issue["key"]] = number
        save_state(state)
        print(f"  created leaf  {issue['key']:>8} as #{number}")

    # 2) Create epics with child checklists fully resolved.
    for issue in issues:
        if issue["kind"] != "epic":
            continue
        if issue["key"] in state:
            continue
        body = assemble_body(issue, state=state, all_issues=issues)
        number = create_issue(
            title=issue["title"],
            body=body,
            labels=issue["labels"],
            available_labels=available_labels,
        )
        state[issue["key"]] = number
        save_state(state)
        print(f"  created epic  {issue['key']:>8} as #{number}")

    # 3) Create the roadmap last with the full epic checklist.
    for issue in issues:
        if issue["kind"] != "roadmap":
            continue
        if issue["key"] in state:
            continue
        body = assemble_body(issue, state=state, all_issues=issues)
        number = create_issue(
            title=issue["title"],
            body=body,
            labels=issue["labels"],
            available_labels=available_labels,
        )
        state[issue["key"]] = number
        save_state(state)
        print(f"  created road  {issue['key']:>8} as #{number}")

    print(f"\nDone. State at {STATE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
