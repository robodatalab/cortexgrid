# Grants the existing robolab-dgx IAM user (created by terraform/platform/secrets)
# read/write to this bucket. The user is also reused by ESO/in-cluster boto3
# clients, so any pod with aws-bootstrap-creds inherits the same access.

data "aws_iam_user" "dgx" {
  user_name = "robolab-dgx"
}

data "aws_iam_policy_document" "dgx_s3" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.main.arn]
  }

  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.main.arn}/*"]
  }
}

resource "aws_iam_user_policy" "dgx_s3" {
  name   = "s3-${var.bucket_name}"
  user   = data.aws_iam_user.dgx.user_name
  policy = data.aws_iam_policy_document.dgx_s3.json
}
