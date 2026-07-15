variable "aws_region" {
  description = "AWS region for IAM and DynamoDB ARNs."
  type        = string
  default     = "us-east-1"
}

variable "s3_bucket" {
  description = "Lakehouse bucket (Athena source data + query results)."
  type        = string
  default     = "dn-lakehouse-dev"
}

variable "athena_results_prefix" {
  description = <<-EOT
    S3 key prefix for Athena query results (ATHENA_S3_OUTPUT without s3://bucket/).
    Must match what Streamlit apps and dbt freshness use.
  EOT
  type        = string
  default     = "logs/athena-results"
}

variable "draft_iam_user_name" {
  description = "IAM user for the draft Streamlit app."
  type        = string
  default     = "streamlit-draft-tool"
}

variable "inseason_iam_user_name" {
  description = "IAM user for the in-season Streamlit app."
  type        = string
  default     = "streamlit-inseason-tool"
}

variable "dynamodb_table_prefix" {
  description = <<-EOT
    DynamoDB table name prefix used by the draft tool. The app today uses
    tables named `{prefix}_{session_id}`. Policy resources cover that prefix.
  EOT
  type        = string
  default     = "fantasy_baseball_draft"
}

variable "allow_dynamodb_create_table" {
  description = <<-EOT
    If true, draft user may CreateTable/UpdateTable/DeleteTable on tables
    matching dynamodb_table_prefix*. Keep true until #147 (E1.2) removes
    runtime create_table from the app and tables are provisioned out-of-band.
    Default true so applying this module does not break existing draft sessions.
  EOT
  type    = bool
  default = true
}
