# scripts/

One-shot operational scripts that aren't part of the data platform itself.

## `create_planning_issues.py`

Creates the GitHub planning issues listed in `issues.py` on
`danolen/fantasy-baseball-platform`. Every ticket body is hand-written; the
script just orchestrates creating them in the right order so epics can link
to their children.

### Why this exists

Issue definitions live in git (`scripts/issues.py`) so they can be reviewed
in a PR, edited, and created idempotently. The script creates leaves first,
then epics with child checklists, then the roadmap.

### Agent / token capabilities

| Capability | Cursor integration token (`ghs_…`) | Fine-grained PAT |
|------------|------------------------------------|------------------|
| Create issues | Yes | Yes |
| Create / assign labels | No | Yes |
| Edit / comment / close issues | No | Yes |
| Push feature branches / open PRs | Yes (via git remote) | If Contents/PR scopes granted |

For labeled epics and full issue workflow, use the fine-grained PAT below.

### One-time setup

1. Create a fine-grained GitHub Personal Access Token scoped to **only**
   this repo with:
   - **Issues:** Read and write
   - **Metadata:** Read-only
   - No Administration / Secrets / Workflows access
2. Provide the token to the script (resolved in this order):
   - **AWS Secrets Manager** (preferred for Cloud Agents): secret
     `fantasy-baseball-platform` in `us-east-1`, JSON key
     `gh_pat_issue_and_script_work`. Older aliases (`token`, `GH_PAT`,
     `gh_pat`) still work. Override name/region with
     `GH_PAT_SECRET_NAME` / `GH_PAT_SECRET_REGION`.
   - **`GH_PAT` / `GH_TOKEN` env var** (local runs, or if Secrets Manager
     is unavailable).
   - **Cursor dashboard secret** named `GH_PAT` under
     *Cloud Agents → Secrets*. Cursor only injects secrets when a **new**
     agent VM is provisioned — existing sessions may not see newly-added
     secrets, which is why Secrets Manager is preferred for agents.

### Usage

```sh
# Preview the plan without touching GitHub
python scripts/create_planning_issues.py --dry-run

# Create missing issues (skips keys already in scripts/.issue_state.json)
python scripts/create_planning_issues.py

# Create without attempting label create/assign
python scripts/create_planning_issues.py --skip-labels

# Retro-apply labels from issues.py after granting a PAT
python scripts/create_planning_issues.py --apply-labels-only
```

State is persisted to `scripts/.issue_state.json` so re-runs skip already-
created issues. **This file is committed** so anyone can re-run the script
(for example to retro-apply labels after granting a PAT) without
re-creating the issues. Delete it to start over (or to point the script at
a different repo).

### Tweaking the issues before running

Open `scripts/issues.py`. Every ticket is a `_add(...)` call. To:

- **Edit a ticket:** change the `title`, `labels`, or `body` of the
  matching entry.
- **Add a ticket:** copy an existing `_add(...)` block, give it a new
  `key`, and set `parent_key` to the epic key (or `None` for a standalone
  issue).
- **Remove a ticket:** delete the `_add(...)` block. If it was already
  created, the GitHub issue stays (the script never deletes).

Run `python scripts/create_planning_issues.py --dry-run` to confirm the
plan before creating anything.
