# scripts/

One-shot operational scripts that aren't part of the data platform itself.

## `create_planning_issues.py`

Creates the GitHub planning issues listed in `issues.py` on
`danolen/fantasy-baseball-platform`. Every ticket body is hand-written; the
script just orchestrates creating them in the right order so epics can link
to their children.

### Why this exists

The Cursor Cloud Agent's default GitHub installation token is read-only on
this repo and can't create issues directly. Rather than have me dictate the
issues for you to copy-paste, the script captures every ticket as a code
artifact you can review in a PR, edit if you want, and then run once.

### One-time setup

1. Create a fine-grained GitHub Personal Access Token scoped to this repo
   with **Issues: Read and write** and **Metadata: Read-only** permissions.
2. Choose how you'll run the script:
   - **Locally:** `export GH_PAT=ghp_xxx`
   - **From a Cursor Cloud Agent:** add a Secret named `GH_PAT` in the
     Cursor dashboard under *Cloud Agents → Secrets*. It will be injected
     as an env var on the next agent run.

### Usage

```sh
# Preview the plan without touching GitHub
python scripts/create_planning_issues.py --dry-run

# Create everything
GH_PAT=ghp_xxx python scripts/create_planning_issues.py

# Only refresh epic + roadmap checklists with the current child numbers
# (safe to re-run any time)
python scripts/create_planning_issues.py --relink-only
```

State is persisted to `scripts/.issue_state.json` so re-runs skip already-
created issues. **This file is committed** so anyone can re-run the script
(for example to retro-apply labels after granting a PAT) without
re-creating the issues. Delete it to start over (or to point the script at
a different repo).

### Tweaking the issues before running

Open `scripts/issues.py`. Every ticket is a `_add(...)` call near the top
of the file. To:

- **Edit a ticket:** change the `title`, `labels`, or `body` of the
  matching entry.
- **Add a ticket:** copy an existing `_add(...)` block, give it a new
  `key`, and set `parent_key` to the epic key (or `None` for a standalone
  issue).
- **Remove a ticket:** delete the `_add(...)` block. If it was already
  created, the GitHub issue stays (the script never deletes).

Run `python scripts/create_planning_issues.py --dry-run` to confirm the
plan before creating anything.
