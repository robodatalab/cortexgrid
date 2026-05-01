locals {
  notes_db_name = "notes"

  # Connection URI for the postgres maintenance database, used by the bootstrap
  # provisioners to create the `notes` database. The master user (`robolab`) is
  # the db_owner of every database it creates.
  rds_admin_uri = format(
    "postgresql://%s:%s@%s:%d/postgres?sslmode=require",
    aws_db_instance.main.username,
    random_password.master.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
  )

  notes_uri = format(
    "postgresql://%s:%s@%s:%d/%s?sslmode=require",
    aws_db_instance.main.username,
    random_password.master.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
    local.notes_db_name,
  )
}

# Bootstrap step 1: create the `notes` database if it doesn't exist.
# Idempotent. Requires `psql` on the apply machine and VPC reachability to RDS
# (i.e. the Tailscale subnet router must be up and its route approved).
resource "null_resource" "notes_database" {
  triggers = {
    rds_instance = aws_db_instance.main.id
    db_name      = local.notes_db_name
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      psql "${local.rds_admin_uri}" -tAc "SELECT 1 FROM pg_database WHERE datname='${local.notes_db_name}'" \
        | grep -q '^1$' \
        || psql "${local.rds_admin_uri}" -c 'CREATE DATABASE "${local.notes_db_name}"'
    EOT
  }
}

# Bootstrap step 2: apply the schema. Re-runs whenever the SQL file changes.
resource "null_resource" "notes_schema" {
  depends_on = [null_resource.notes_database]

  triggers = {
    schema_sha = filesha256("${path.module}/notes-schema.sql")
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      psql "${local.notes_uri}" -v ON_ERROR_STOP=1 -f "${path.module}/notes-schema.sql"
    EOT
  }
}

# Connection URI for the cortexflow-ui notes feature.
# Same recovery_window_in_days=0 rationale as MLFLOW_BACKEND_STORE_URI above.
resource "aws_secretsmanager_secret" "notes_db_uri" {
  name                    = "robolab/infra/NOTES_DB_URI"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "notes_db_uri" {
  secret_id     = aws_secretsmanager_secret.notes_db_uri.id
  secret_string = local.notes_uri
}
