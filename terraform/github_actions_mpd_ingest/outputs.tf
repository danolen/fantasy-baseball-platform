output "github_actions_role_arn" {
  description = "Set as GitHub repository variable AWS_GHA_MPD_INGEST_ROLE_ARN for the ingest workflow."
  value       = aws_iam_role.mpd_ingest.arn
}

output "github_actions_role_name" {
  description = "IAM role name for reference."
  value       = aws_iam_role.mpd_ingest.name
}

output "github_oidc_provider_arn" {
  description = "GitHub OIDC provider ARN used by the role trust policy."
  value       = local.github_oidc_provider_arn
}

output "s3_upload_prefix" {
  description = "S3 prefix where the workflow uploads the CSV."
  value       = "s3://${var.s3_bucket}/${local.s3_prefix_normalized}/"
}
