locals {
  notes_db_name = "notes"

  # Connection URI for the postgres maintenance database, used by the bootstrap
  # provisioners to create the `notes` database. The master user (`robolab`) is
  # the db_owner of every database it creates. `connect_timeout=10` keeps each
  # retry iteration short so the loop reacts quickly to a flaky tailnet path.
  rds_admin_uri = format(
    "postgresql://%s:%s@%s:%d/postgres?sslmode=require&connect_timeout=10",
    aws_db_instance.main.username,
    random_password.master.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
  )

  notes_uri = format(
    "postgresql://%s:%s@%s:%d/%s?sslmode=require&connect_timeout=10",
    aws_db_instance.main.username,
    random_password.master.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
    local.notes_db_name,
  )
}

# Bootstrap step 1: create the `notes` database if it doesn't exist.
# Idempotent. Requires `psql` on the apply machine and VPC reachability to RDS,
# i.e. the Tailscale subnet router up + the advertised route auto-approved by
# the tailnet ACL `autoApprovers` block (see terraform/platform/README.md).
# The retry loop absorbs router boot + Tailscale propagation latency, so a
# cold-start `./cg add head --aws` succeeds in a single pass.
resource "null_resource" "notes_database" {
  triggers = {
    rds_instance = aws_db_instance.main.id
    router       = var.router_instance_id
    db_name      = local.notes_db_name
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      deadline=$(( $(date +%s) + 600 ))
      until psql "${local.rds_admin_uri}" -tAc 'SELECT 1' >/dev/null 2>&1; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
          echo "Timed out waiting for RDS reachability via Tailscale (10m)" >&2
          exit 1
        fi
        echo "Waiting for RDS via tailnet (router boot + route propagation)..." >&2
        sleep 10
      done
      psql "${local.rds_admin_uri}" -tAc "SELECT 1 FROM pg_database WHERE datname='${local.notes_db_name}'" \
        | grep -q '^1$' \
        || psql "${local.rds_admin_uri}" -c 'CREATE DATABASE "${local.notes_db_name}"'
    EOT
  }
}

# Bootstrap step 2: apply the schema. Re-runs whenever the SQL file changes.
# RDS reachability is already established by step 1, so no retry is needed.
resource "null_resource" "notes_schema" {
  depends_on = [null_resource.notes_database]

  triggers = {
    schema_sha = filesha256("${path.module}/../../../k8s/charts/cortexgrid/files/postgres/notes_schema.sql")
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      deadline=$(( $(date +%s) + 600 ))
      until psql "${local.notes_uri}" -tAc 'SELECT 1' >/dev/null 2>&1; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
          echo "Timed out waiting for notes DB reachability via Tailscale (10m)" >&2
          exit 1
        fi
        echo "Waiting for notes DB via tailnet..." >&2
        sleep 10
      done
      psql "${local.notes_uri}" -v ON_ERROR_STOP=1 -f "${path.module}/../../../k8s/charts/cortexgrid/files/postgres/notes_schema.sql"
    EOT
  }
}
