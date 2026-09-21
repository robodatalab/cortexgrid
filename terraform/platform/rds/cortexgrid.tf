locals {
  cortexgrid_db_name = "cortexgrid"

  cortexgrid_uri = format(
    "postgresql://%s:%s@%s:%d/%s?sslmode=require&connect_timeout=10",
    aws_db_instance.main.username,
    random_password.master.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
    local.cortexgrid_db_name,
  )
}

# The `cortexgrid` database: the records the jobs control plane keeps
# (experiments, runs, jobs, model registry, deployments). Bootstrapped exactly
# like `notes` (see notes.tf): create the database if missing, then apply the
# schema whenever the SQL file changes.
resource "null_resource" "cortexgrid_database" {
  triggers = {
    rds_instance = aws_db_instance.main.id
    router       = var.router_instance_id
    db_name      = local.cortexgrid_db_name
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
      psql "${local.rds_admin_uri}" -tAc "SELECT 1 FROM pg_database WHERE datname='${local.cortexgrid_db_name}'" \
        | grep -q '^1$' \
        || psql "${local.rds_admin_uri}" -c 'CREATE DATABASE "${local.cortexgrid_db_name}"'
    EOT
  }
}

resource "null_resource" "cortexgrid_schema" {
  depends_on = [null_resource.cortexgrid_database]

  triggers = {
    schema_sha = filesha256("${path.module}/../../../k8s/charts/cortexgrid/files/postgres/cortexgrid_schema.sql")
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      deadline=$(( $(date +%s) + 600 ))
      until psql "${local.cortexgrid_uri}" -tAc 'SELECT 1' >/dev/null 2>&1; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
          echo "Timed out waiting for cortexgrid DB reachability via Tailscale (10m)" >&2
          exit 1
        fi
        echo "Waiting for cortexgrid DB via tailnet..." >&2
        sleep 10
      done
      psql "${local.cortexgrid_uri}" -v ON_ERROR_STOP=1 -f "${path.module}/../../../k8s/charts/cortexgrid/files/postgres/cortexgrid_schema.sql"
    EOT
  }
}
