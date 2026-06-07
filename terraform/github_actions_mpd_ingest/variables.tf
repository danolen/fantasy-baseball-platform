variable "aws_region" {
  description = "AWS region for IAM resources."
  type        = string
  default     = "us-east-1"
}

variable "github_org" {
  description = "GitHub organization or user that owns the repository."
  type        = string
  default     = "danolen"
}

variable "github_repo" {
  description = "GitHub repository name (without org)."
  type        = string
  default     = "fantasy-baseball-platform"
}

variable "github_default_branch" {
  description = "Branch allowed to assume the ingest role (e.g. master)."
  type        = string
  default     = "master"
}

variable "s3_bucket" {
  description = "Lakehouse bucket that receives the player ID map CSV."
  type        = string
  default     = "dn-lakehouse-dev"
}

variable "s3_object_prefix" {
  description = "S3 key prefix for the player ID map (no leading slash; trailing slash optional)."
  type        = string
  default     = "mapping/mpd_player_id_map"
}

variable "create_github_oidc_provider" {
  description = <<-EOT
    Create the account-wide GitHub OIDC provider. Set to false if this account
    already has token.actions.githubusercontent.com registered (then set
    github_oidc_provider_arn).
  EOT
  type    = bool
  default = true
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN when create_github_oidc_provider is false."
  type        = string
  default     = ""

  validation {
    condition = (
      var.create_github_oidc_provider
      || trimspace(var.github_oidc_provider_arn) != ""
    )
    error_message = "Set github_oidc_provider_arn when create_github_oidc_provider is false."
  }
}

variable "role_name" {
  description = "IAM role name assumed by GitHub Actions for MPD ingest."
  type        = string
  default     = "github-actions-mpd-player-map-ingest"
}
