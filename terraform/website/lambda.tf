resource "aws_security_group" "lambda" {
  name        = "robolab-auth-lambda"
  description = "Auth Lambda - outbound to RDS and internet (SES via NAT)"
  vpc_id      = aws_vpc.auth.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Package the Lambda source into a zip at plan time.
# Run `npm ci --omit=dev` in lambda/auth/ before `terraform apply`
# to populate node_modules, then Terraform will re-zip on any source change.
data "archive_file" "auth_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../lambda/auth"
  output_path = "${path.module}/../../lambda/auth.zip"
  excludes    = ["schema.sql"]
}

resource "aws_lambda_function" "auth" {
  function_name    = "robolab-auth"
  role             = aws_iam_role.lambda.arn
  runtime          = "nodejs22.x"
  handler          = "index.handler"
  filename         = data.archive_file.auth_lambda.output_path
  source_code_hash = data.archive_file.auth_lambda.output_base64sha256
  timeout          = 15
  memory_size      = 256

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      DB_HOST     = aws_db_instance.auth.address
      DB_NAME     = var.db_name
      DB_USER     = var.db_username
      DB_PASSWORD = var.db_password  # also stored in Secrets Manager for rotation
      JWT_SECRET  = var.jwt_secret
      ADMIN_EMAIL = var.admin_email
      FROM_EMAIL  = var.from_email
      SITE_URL    = "https://${var.domain_name}"
    }
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_basic]
}

resource "aws_cloudwatch_log_group" "auth_lambda" {
  name              = "/aws/lambda/robolab-auth"
  retention_in_days = 30
}
