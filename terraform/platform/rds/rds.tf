resource "random_password" "master" {
  length  = 32
  special = false
}

# Composed mlflow backend store URI — the only SM entry this module publishes.
# Single value, consumed directly by mlflow's `mlflow-config` ExternalSecret
# (no ESO templating). On-prem writes the same key with an in-cluster Postgres
# URI (see k8s/seed/operators/platform_config.py), so mlflow's manifest is
# profile-agnostic. The master password lives only inside this composed URI
# and the RDS instance itself.
resource "aws_secretsmanager_secret" "mlflow_backend_store_uri" {
  name = "robolab/infra/MLFLOW_BACKEND_STORE_URI"
}

resource "aws_secretsmanager_secret_version" "mlflow_backend_store_uri" {
  secret_id = aws_secretsmanager_secret.mlflow_backend_store_uri.id
  secret_string = format(
    "postgresql://%s:%s@%s:%d/%s",
    aws_db_instance.main.username,
    random_password.master.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
    aws_db_instance.main.db_name,
  )
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

  backup_retention_period    = 0
  skip_final_snapshot        = true
  deletion_protection        = false
  apply_immediately          = true
  auto_minor_version_upgrade = true

  tags = {
    Project   = "robolab"
    Component = "rds"
  }
}
