# robolab-dgx: the IAM user whose access key the cluster uses for S3 on the AWS
# profile. Head setup publishes the key to the head secrets store as S3_*
# (k8s/seed/operators/terraform_outputs.py). Its S3 policy lives in ../s3.

resource "aws_iam_user" "dgx" {
  name = "robolab-dgx"
  tags = { Project = "robolab", Component = "training" }
}

resource "aws_iam_access_key" "dgx" {
  user = aws_iam_user.dgx.name
}
