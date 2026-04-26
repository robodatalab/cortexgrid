# Generate a random master password and stash it in Secrets Manager under the
# robolab/infra/* namespace. ESO inside the cluster reads it via the existing
# ClusterSecretStore — no separate IAM grant needed.

resource "random_password" "master" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "rds_password" {
  name = "robolab/infra/RDS_PASSWORD"
}

resource "aws_secretsmanager_secret_version" "rds_password" {
  secret_id     = aws_secretsmanager_secret.rds_password.id
  secret_string = random_password.master.result
}

# Connection info also lives in SM so apps can read it via ESO without
# hardcoding terraform-controlled values into manifests.

resource "aws_secretsmanager_secret" "rds_host" {
  name = "robolab/infra/RDS_HOST"
}

resource "aws_secretsmanager_secret_version" "rds_host" {
  secret_id     = aws_secretsmanager_secret.rds_host.id
  secret_string = aws_db_instance.main.address
}

resource "aws_secretsmanager_secret" "rds_port" {
  name = "robolab/infra/RDS_PORT"
}

resource "aws_secretsmanager_secret_version" "rds_port" {
  secret_id     = aws_secretsmanager_secret.rds_port.id
  secret_string = tostring(aws_db_instance.main.port)
}

resource "aws_secretsmanager_secret" "rds_username" {
  name = "robolab/infra/RDS_USERNAME"
}

resource "aws_secretsmanager_secret_version" "rds_username" {
  secret_id     = aws_secretsmanager_secret.rds_username.id
  secret_string = aws_db_instance.main.username
}

resource "aws_secretsmanager_secret" "rds_db_name" {
  name = "robolab/infra/RDS_DB_NAME"
}

resource "aws_secretsmanager_secret_version" "rds_db_name" {
  secret_id     = aws_secretsmanager_secret.rds_db_name.id
  secret_string = aws_db_instance.main.db_name
}

resource "aws_db_instance" "main" {
  identifier             = "robolab"
  engine                 = "postgres"
  engine_version         = var.engine_version
  instance_class         = var.instance_class
  allocated_storage      = var.allocated_storage_gb
  storage_type           = "gp3"
  storage_encrypted      = true
  db_name                = var.db_name
  username               = "robolab"
  password               = random_password.master.result
  port                   = 5432
  multi_az               = false
  publicly_accessible    = false
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period   = 0
  skip_final_snapshot       = true
  deletion_protection       = false
  apply_immediately         = true
  auto_minor_version_upgrade = true

  tags = {
    Project   = "robolab"
    Component = "rds"
  }
}
