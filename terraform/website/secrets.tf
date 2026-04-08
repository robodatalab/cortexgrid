# Secrets are managed centrally in terraform/platform/secrets/.
# These data sources look up the ARNs for IAM policy references.

data "aws_secretsmanager_secret" "jwt" {
  name = "robolab/auth/jwt-secret"
}

data "aws_secretsmanager_secret" "db_password" {
  name = "robolab/auth/db-password"
}
