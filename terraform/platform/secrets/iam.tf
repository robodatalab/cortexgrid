# =============================================================================
# IAM — access policies for secrets consumers
# =============================================================================

resource "aws_iam_user" "dgx" {
  name = "robolab-dgx"
  tags = { Project = "robolab", Component = "training" }
}

resource "aws_iam_access_key" "dgx" {
  user = aws_iam_user.dgx.name
}

data "aws_iam_policy_document" "dgx_secrets_manage" {
  statement {
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
      "secretsmanager:CreateSecret",
      "secretsmanager:PutSecretValue",
      "secretsmanager:DeleteSecret",
    ]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:*:secret:robolab/infra/*"]
  }

  statement {
    actions   = ["secretsmanager:ListSecrets"]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "dgx_secrets_manage" {
  name   = "secrets-manage"
  user   = aws_iam_user.dgx.name
  policy = data.aws_iam_policy_document.dgx_secrets_manage.json
}

# ── DGX → argocd/ secrets (manage + read) ───────────────────────────────────
# Seed publishes .env here; ESO running in-cluster reads these using the same
# robolab-dgx credentials.

data "aws_iam_policy_document" "dgx_argocd_secrets_manage" {
  statement {
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:PutSecretValue",
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
    ]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:*:secret:robolab/argocd/*"]
  }
}

resource "aws_iam_user_policy" "dgx_argocd_secrets_manage" {
  name   = "argocd-secrets-manage"
  user   = aws_iam_user.dgx.name
  policy = data.aws_iam_policy_document.dgx_argocd_secrets_manage.json
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
