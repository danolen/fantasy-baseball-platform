data "aws_caller_identity" "current" {}

locals {
  athena_results_key = trim(var.athena_results_prefix, "/")
  athena_results_arn = "arn:aws:s3:::${var.s3_bucket}/${local.athena_results_key}/*"
  lakehouse_arn      = "arn:aws:s3:::${var.s3_bucket}/*"
  lakehouse_bucket   = "arn:aws:s3:::${var.s3_bucket}"

  account_id = data.aws_caller_identity.current.account_id

  # Draft tool tables: fantasy_baseball_draft_<session_id>
  dynamodb_table_arns = [
    "arn:aws:dynamodb:${var.aws_region}:${local.account_id}:table/${var.dynamodb_table_prefix}",
    "arn:aws:dynamodb:${var.aws_region}:${local.account_id}:table/${var.dynamodb_table_prefix}_*",
  ]
}

# ---------------------------------------------------------------------------
# Shared Athena / Glue / S3-results read+write (both Streamlit apps)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "athena_read" {
  statement {
    sid    = "AthenaQuery"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "GlueReadCatalog"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]
    resources = [
      "arn:aws:glue:${var.aws_region}:${local.account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${local.account_id}:database/*",
      "arn:aws:glue:${var.aws_region}:${local.account_id}:table/*/*",
    ]
  }

  statement {
    sid    = "ReadLakehouseData"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      local.lakehouse_bucket,
      local.lakehouse_arn,
    ]
  }

  statement {
    sid    = "AthenaResultsBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [local.lakehouse_bucket]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "${local.athena_results_key}/*",
        local.athena_results_key,
      ]
    }
  }

  statement {
    sid    = "AthenaResultsObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [local.athena_results_arn]
  }
}

# ---------------------------------------------------------------------------
# Draft tool: Athena read + DynamoDB on draft session tables
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "draft_dynamodb" {
  # ListTables cannot be scoped to a table ARN; required for the session picker
  # until #147 redesigns session discovery.
  statement {
    sid       = "ListDraftTables"
    effect    = "Allow"
    actions   = ["dynamodb:ListTables"]
    resources = ["*"]
  }

  statement {
    sid    = "DraftTableReadWrite"
    effect = "Allow"
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Scan",
      "dynamodb:Query",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = local.dynamodb_table_arns
  }

  dynamic "statement" {
    for_each = var.allow_dynamodb_create_table ? [1] : []
    content {
      sid    = "DraftTableLifecycleInterim"
      effect = "Allow"
      actions = [
        "dynamodb:CreateTable",
        "dynamodb:UpdateTable",
        "dynamodb:DeleteTable",
        "dynamodb:TagResource",
      ]
      resources = local.dynamodb_table_arns
    }
  }
}

resource "aws_iam_user" "draft" {
  name = var.draft_iam_user_name
  path = "/streamlit/"

  tags = {
    Project = "fantasy-baseball-platform"
    App     = "draft-tool"
    Ticket  = "145"
  }
}

resource "aws_iam_user_policy" "draft_athena" {
  name   = "athena-read"
  user   = aws_iam_user.draft.name
  policy = data.aws_iam_policy_document.athena_read.json
}

resource "aws_iam_user_policy" "draft_dynamodb" {
  name   = "dynamodb-draft-sessions"
  user   = aws_iam_user.draft.name
  policy = data.aws_iam_policy_document.draft_dynamodb.json
}

# ---------------------------------------------------------------------------
# In-season tool: Athena read only (no DynamoDB)
# ---------------------------------------------------------------------------

resource "aws_iam_user" "inseason" {
  name = var.inseason_iam_user_name
  path = "/streamlit/"

  tags = {
    Project = "fantasy-baseball-platform"
    App     = "in-season-tool"
    Ticket  = "145"
  }
}

resource "aws_iam_user_policy" "inseason_athena" {
  name   = "athena-read"
  user   = aws_iam_user.inseason.name
  policy = data.aws_iam_policy_document.athena_read.json
}
