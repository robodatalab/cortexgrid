# =============================================================================
# IAM — the robolab-dgx user: the cluster's AWS identity for Route53 (both
# profiles) and S3 (AWS profile). Head setup publishes its keys to the head
# secrets store (k8s/seed/operators/terraform_outputs.py).
# =============================================================================

resource "aws_iam_user" "dgx" {
  name = "robolab-dgx"
  tags = { Project = "robolab", Component = "training" }
}

resource "aws_iam_access_key" "dgx" {
  user = aws_iam_user.dgx.name
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
# The CI role that used it only read AWS Secrets Manager and is gone. The
# provider stays: an AWS account holds one provider per URL, and other stacks
# in the account may federate GitHub Actions through it.

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["ffffffffffffffffffffffffffffffffffffffff"]
}
