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
