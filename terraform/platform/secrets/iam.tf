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

# ── DGX -> Route 53 (cert-manager ACME DNS-01 challenges) ────────────────────
# cert-manager writes _acme-challenge TXT records under robodatalab.com to
# prove ownership to Let's Encrypt before the wildcard cert is issued.

data "aws_route53_zone" "robodatalab" {
  name = "robodatalab.com"
}

data "aws_iam_policy_document" "dgx_route53_acme" {
  statement {
    actions   = ["route53:GetChange"]
    resources = ["arn:aws:route53:::change/*"]
  }
  statement {
    actions   = ["route53:ChangeResourceRecordSets", "route53:ListResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${data.aws_route53_zone.robodatalab.zone_id}"]
  }
  statement {
    actions   = ["route53:ListHostedZonesByName"]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "dgx_route53_acme" {
  name   = "route53-acme"
  user   = aws_iam_user.dgx.name
  policy = data.aws_iam_policy_document.dgx_route53_acme.json
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

# Integration tests create/update/delete throwaway secrets named robolab/infra/it-*.
# Scoped tightly so CI cannot touch production secrets.

data "aws_iam_policy_document" "github_actions_it_secrets_manage" {
  statement {
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:CreateSecret",
      "secretsmanager:PutSecretValue",
      "secretsmanager:DeleteSecret",
    ]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:*:secret:robolab/infra/it-*"]
  }
}

resource "aws_iam_role_policy" "github_actions_it_secrets_manage" {
  name   = "it-secrets-manage"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_it_secrets_manage.json
}

# Surface the role ARN in SM so Argo Notifications can pass it through the
# webhook payload to GitHub workflows, which then assume this role via OIDC.

resource "aws_secretsmanager_secret" "github_actions_role_arn" {
  name                    = "robolab/infra/AWS_ROLE_ARN"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "github_actions_role_arn" {
  secret_id     = aws_secretsmanager_secret.github_actions_role_arn.id
  secret_string = aws_iam_role.github_actions.arn
}
