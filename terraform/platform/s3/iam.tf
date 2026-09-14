# Grants the robolab-dgx IAM user (created by the head module) full
# read/write/create/delete on any S3 bucket in the account. cortexgrid
# auto-creates buckets on demand under arbitrary names, so the policy is
# account-wide rather than scoped to a single bucket. Pods get the user's key
# through the s3-creds Secret.

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
  user   = var.dgx_user_name
  policy = data.aws_iam_policy_document.dgx_s3.json
}
