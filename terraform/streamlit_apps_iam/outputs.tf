output "draft_iam_user_name" {
  description = "IAM user for the draft Streamlit app. Create access keys out-of-band."
  value       = aws_iam_user.draft.name
}

output "draft_iam_user_arn" {
  description = "ARN of the draft Streamlit IAM user."
  value       = aws_iam_user.draft.arn
}

output "inseason_iam_user_name" {
  description = "IAM user for the in-season Streamlit app. Create access keys out-of-band."
  value       = aws_iam_user.inseason.name
}

output "inseason_iam_user_arn" {
  description = "ARN of the in-season Streamlit IAM user."
  value       = aws_iam_user.inseason.arn
}

output "athena_s3_output" {
  description = "ATHENA_S3_OUTPUT value these policies assume (must match Streamlit Secrets)."
  value       = "s3://${var.s3_bucket}/${local.athena_results_key}/"
}

output "dynamodb_table_prefix" {
  description = "DynamoDB table prefix allowed for the draft user."
  value       = var.dynamodb_table_prefix
}

output "allow_dynamodb_create_table" {
  description = "Whether CreateTable is still granted (interim until #147)."
  value       = var.allow_dynamodb_create_table
}

output "create_access_keys_commands" {
  description = "Commands to create access keys after apply (do not store keys in Terraform state)."
  value       = <<-EOT
    aws iam create-access-key --user-name ${aws_iam_user.draft.name}
    aws iam create-access-key --user-name ${aws_iam_user.inseason.name}
  EOT
}
