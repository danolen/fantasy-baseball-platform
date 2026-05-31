data "aws_caller_identity" "current" {}

locals {
  github_oidc_url      = "https://token.actions.githubusercontent.com"
  github_repo_subject  = "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/${var.github_default_branch}"
  s3_prefix_normalized = trim(var.s3_object_prefix, "/")
  s3_object_arn        = "arn:aws:s3:::${var.s3_bucket}/${local.s3_prefix_normalized}/*"
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

resource "aws_iam_role" "mpd_ingest" {
  name               = var.role_name
  assume_role_policy = data.aws_iam_policy_document.github_assume_role.json
}

data "aws_iam_policy_document" "mpd_ingest_s3" {
  statement {
    sid    = "PutPlayerIdMap"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = [local.s3_object_arn]
  }
}

resource "aws_iam_role_policy" "mpd_ingest_s3" {
  name   = "mpd-player-map-s3-upload"
  role   = aws_iam_role.mpd_ingest.id
  policy = data.aws_iam_policy_document.mpd_ingest_s3.json
}
