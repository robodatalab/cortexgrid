output "mlflow_backend_store_uri" {
  description = "Postgres URI for the mlflow backend store"
  value = format(
    "postgresql://%s:%s@%s:%d/%s",
    aws_db_instance.main.username,
    random_password.master.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
    aws_db_instance.main.db_name,
  )
  sensitive = true
}

output "notes_db_uri" {
  description = "Postgres URI for the cortexgrid-ui notes database"
  value       = local.notes_uri
  sensitive   = true
}

output "endpoint" {
  description = "RDS connection endpoint (host:port)"
  value       = aws_db_instance.main.endpoint
}
