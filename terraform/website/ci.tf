# GitHub Actions OIDC provider (one per AWS account — shared across repos)
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["ffffffffffffffffffffffffffffffffffffffff"]
}

# IAM role assumed by GitHub Actions to deploy the website
resource "aws_iam_role" "website_deploy" {
  name = "robolab-website-deploy"

  assume_role_policy = data.aws_iam_policy_document.github_actions_assume.json

  tags = {
    Project   = "robolab"
    Component = "website"
  }
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
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role_policy" "website_deploy" {
  name   = "website-deploy"
  role   = aws_iam_role.website_deploy.id
  policy = data.aws_iam_policy_document.deploy_permissions.json
}

data "aws_iam_policy_document" "deploy_permissions" {
  # S3: sync website files
  statement {
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.website.arn,
      "${aws_s3_bucket.website.arn}/*",
    ]
  }

  # CloudFront: invalidate cache
  statement {
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.website.arn]
  }
}

output "deploy_role_arn" {
  description = "IAM role ARN for GitHub Actions (set as AWS_DEPLOY_ROLE_ARN secret)"
  value       = aws_iam_role.website_deploy.arn
}
