# Infrastructure (Terraform)

Incremental Terraform modules for this project. Start with the state backend, then apply each module.

| Path | Purpose |
|------|---------|
| [bootstrap/](bootstrap/README.md) | One-time S3 + DynamoDB for remote state |
| [github_actions_mpd_ingest/](github_actions_mpd_ingest/README.md) | OIDC + IAM role for weekly MPD CSV upload (#42) |

Future modules (planning issues): `terraform/prefect/` (A4.1), full AWS import (A7.1).
