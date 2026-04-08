# =============================================================================
# IAM — access policies for secrets consumers
# =============================================================================
#
# Secret values are created by scripts/push-secrets.sh. This module grants
# read access using wildcard ARN patterns so it can be applied before or
# after secrets exist.

# ── DGX Machine User ─────────────────────────────────────────────────────────
# Minimal IAM user for the DGX Spark to pull its own secrets.
# Credentials are the one bootstrapping secret stored manually on the DGX.

resource "aws_iam_user" "dgx" {
  name = "robolab-dgx"
  tags = { Project = "robolab", Component = "training" }
}

resource "aws_iam_access_key" "dgx" {
  user = aws_iam_user.dgx.name
}

data "aws_iam_policy_document" "dgx_secrets_read" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:*:secret:robolab/infra/*"]
  }
}

resource "aws_iam_user_policy" "dgx_secrets_read" {
  name   = "secrets-read"
  user   = aws_iam_user.dgx.name
  policy = data.aws_iam_policy_document.dgx_secrets_read.json
}

# ── GitHub Actions OIDC ──────────────────────────────────────────────────────
# Allows CI pipelines to assume a role and read secrets — no static keys in GH.

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["ffffffffffffffffffffffffffffffffffffffff"]
}

data "aws_iam_policy_document" "github_actions_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for repo in var.github_repos : "repo:${repo}:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "robolab-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume.json
  tags               = { Project = "robolab", Component = "ci" }
}

data "aws_iam_policy_document" "github_actions_secrets_read" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:*:secret:robolab/*"]
  }
}

resource "aws_iam_role_policy" "github_actions_secrets_read" {
  name   = "secrets-read"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_secrets_read.json
}
