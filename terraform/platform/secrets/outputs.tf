output "dgx_user_access_key_id" {
  description = "AWS access key ID for the DGX IAM user (Route53 + S3)"
  value       = aws_iam_access_key.dgx.id
}

output "dgx_user_secret_access_key" {
  description = "AWS secret access key for the DGX IAM user"
  value       = aws_iam_access_key.dgx.secret
  sensitive   = true
}
