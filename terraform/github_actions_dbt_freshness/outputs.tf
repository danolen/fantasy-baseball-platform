output "github_actions_role_arn" {
  description = "Set as GitHub repository variable AWS_GHA_DBT_FRESHNESS_ROLE_ARN for the freshness workflow."
  value       = aws_iam_role.dbt_freshness.arn
}

output "github_actions_role_name" {
  description = "IAM role name for reference."
  value       = aws_iam_role.dbt_freshness.name
}

output "github_oidc_provider_arn" {
  description = "GitHub OIDC provider ARN used by the role trust policy."
  value       = local.github_oidc_provider_arn
}

output "athena_s3_output" {
  description = "Set as GitHub repository variable ATHENA_S3_OUTPUT for the freshness workflow."
  value       = "s3://${var.s3_bucket}/${local.athena_results_key}/"
}
