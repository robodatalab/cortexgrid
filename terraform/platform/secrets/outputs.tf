output "dgx_user_access_key_id" {
  description = "AWS access key ID for the DGX IAM user — the one bootstrap credential"
  value       = aws_iam_access_key.dgx.id
}

output "dgx_user_secret_access_key" {
  description = "AWS secret access key for the DGX IAM user"
  value       = aws_iam_access_key.dgx.secret
  sensitive   = true
}

output "github_actions_role_arn" {
  description = "IAM role ARN for GitHub Actions — set as a repo variable"
  value       = aws_iam_role.github_actions.arn
}
