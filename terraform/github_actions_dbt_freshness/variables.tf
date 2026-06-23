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
  description = "Branch allowed to assume the freshness role (e.g. master)."
  type        = string
  default     = "master"
}

variable "s3_bucket" {
  description = "Lakehouse bucket that holds vendor source data."
  type        = string
  default     = "dn-lakehouse-dev"
}

variable "athena_results_prefix" {
  description = "S3 key prefix for Athena query results (ATHENA_S3_OUTPUT without bucket)."
  type        = string
  default     = "athena-results"
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
  description = "IAM role name assumed by GitHub Actions for dbt source freshness."
  type        = string
  default     = "github-actions-dbt-source-freshness"
}
