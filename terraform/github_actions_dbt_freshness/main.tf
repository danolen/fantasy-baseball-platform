data "aws_caller_identity" "current" {}

locals {
  github_oidc_url     = "https://token.actions.githubusercontent.com"
  github_repo_subject = "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/${var.github_default_branch}"
  athena_results_key  = trim(var.athena_results_prefix, "/")
  athena_results_arn  = "arn:aws:s3:::${var.s3_bucket}/${local.athena_results_key}/*"
  lakehouse_arn       = "arn:aws:s3:::${var.s3_bucket}/*"
  lakehouse_bucket    = "arn:aws:s3:::${var.s3_bucket}"
}

data "tls_certificate" "github_actions" {
  url = local.github_oidc_url
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url             = local.github_oidc_url
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = data.tls_certificate.github_actions.certificates[*].sha1_fingerprint
}

locals {
  github_oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.github_oidc_provider_arn
}

data "aws_iam_policy_document" "github_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_repo_subject]
    }
  }
}

resource "aws_iam_role" "dbt_freshness" {
  name               = var.role_name
  assume_role_policy = data.aws_iam_policy_document.github_assume_role.json
}

data "aws_iam_policy_document" "dbt_freshness" {
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
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/*/*",
    ]
  }

  statement {
    sid    = "ReadLakehouseData"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]
    resources = [
      local.lakehouse_bucket,
      local.lakehouse_arn,
    ]
  }

  statement {
    sid    = "AthenaResultsStaging"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      "arn:aws:s3:::${var.s3_bucket}/${local.athena_results_key}",
      local.athena_results_arn,
    ]
  }
}

resource "aws_iam_role_policy" "dbt_freshness" {
  name   = "dbt-source-freshness"
  role   = aws_iam_role.dbt_freshness.id
  policy = data.aws_iam_policy_document.dbt_freshness.json
}
