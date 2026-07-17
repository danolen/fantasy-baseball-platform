#!/usr/bin/env python3
"""
Verify the fine-grained GitHub PAT used for agent/issue script work (#152).

Resolves the token the same way as ``create_planning_issues.py`` (Secrets
Manager key ``gh_pat_issue_and_script_work`` preferred), then:

1. Confirms ``gh api user`` works.
2. Creates a throwaway issue with a ``chore`` label.
3. Comments on it.
4. Closes it.

Usage
-----

    # Needs AWS creds that can read fantasy-baseball-platform in us-east-1
    python scripts/verify_gh_issue_pat.py

    # Or pass a token explicitly (local debugging only — do not commit)
    GH_PAT=github_pat_xxx python scripts/verify_gh_issue_pat.py

Exit 0 on success. Does not print the token.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from create_planning_issues import REPO, _resolve_gh_pat  # noqa: E402


def _gh(token: str, *args: str) -> str:
    import os

    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env.pop("GH_PAT", None)
    result = subprocess.run(
        ["gh", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()


def main() -> int:
    token = _resolve_gh_pat()
    if not token:
        print(
            "FAIL: no token. Set GH_PAT or ensure Secrets Manager secret "
            "fantasy-baseball-platform has key gh_pat_issue_and_script_work.",
            file=sys.stderr,
        )
        return 1
    if not token.startswith("github_pat_"):
        print(
            "FAIL: resolved token does not look like a fine-grained PAT "
            f"(prefix={token[:12]!r}). Prefer github_pat_… not a classic ghp_… "
            "or installation ghs_… token.",
            file=sys.stderr,
        )
        return 1

    login = _gh(token, "api", "user", "--jq", ".login")
    print(f"OK authenticated as {login}")

    url = _gh(
        token,
        "issue",
        "create",
        "--repo",
        REPO,
        "--title",
        "[TEST] verify_gh_issue_pat probe — DELETE",
        "--body",
        "Automatic probe from scripts/verify_gh_issue_pat.py (#152). Safe to close.",
        "--label",
        "chore",
    )
    print(f"OK create+label {url}")
    number = url.rstrip("/").split("/")[-1]

    _gh(
        token,
        "issue",
        "comment",
        number,
        "--repo",
        REPO,
        "--body",
        "verify_gh_issue_pat: comment OK",
    )
    print(f"OK comment #{number}")

    _gh(
        token,
        "issue",
        "close",
        number,
        "--repo",
        REPO,
        "--reason",
        "not planned",
        "--comment",
        "verify_gh_issue_pat: closing probe.",
    )
    print(f"OK close #{number}")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
