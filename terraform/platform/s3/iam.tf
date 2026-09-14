# Grants the existing robolab-dgx IAM user (created by terraform/platform/secrets)
# full read/write/create/delete on any S3 bucket in the account. cortexgrid
# auto-creates buckets on demand under arbitrary names, so the policy is
# account-wide rather than scoped to a single bucket. Same key is used by
# ESO/in-cluster boto3 clients, so all pods with aws-bootstrap-creds inherit
# this access.

data "aws_iam_user" "dgx" {
  user_name = "robolab-dgx"
}

data "aws_iam_policy_document" "dgx_s3" {
  statement {
    actions = [
      "s3:ListBucket",
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:GetBucketLocation",
    ]
    resources = ["arn:aws:s3:::*"]
  }

  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["arn:aws:s3:::*/*"]
  }
}

resource "aws_iam_user_policy" "dgx_s3" {
  name   = "s3-${var.bucket_name}"
  user   = data.aws_iam_user.dgx.user_name
  policy = data.aws_iam_policy_document.dgx_s3.json
}
