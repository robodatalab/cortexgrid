output "head_instance_ids" {
  description = "Map of head node name to EC2 instance ID"
  value       = module.head.instance_ids
}

output "head_ebs_volume_ids" {
  description = "Map of head node name to EBS volume ID for /storage; use with `aws ec2 modify-volume` to resize"
  value       = module.head.ebs_volume_ids
}

output "s3_bucket_name" {
  description = "Data + mlflow-artifacts bucket"
  value       = module.s3.bucket_name
}

output "s3_endpoint_url" {
  description = "Regional S3 endpoint, published as S3_ENDPOINT_URL by head setup"
  value       = "https://s3.${var.aws_region}.amazonaws.com"
}

output "s3_region" {
  description = "Region for S3 request signing, published as S3_REGION by head setup"
  value       = var.aws_region
}

output "s3_access_key_id" {
  description = "robolab-dgx access key ID, published as S3_ACCESS_KEY_ID by head setup"
  value       = module.head.dgx_access_key_id
}

output "s3_secret_access_key" {
  description = "robolab-dgx secret access key, published as S3_SECRET_ACCESS_KEY by head setup"
  value       = module.head.dgx_secret_access_key
  sensitive   = true
}

output "mlflow_backend_store_uri" {
  description = "Published as MLFLOW_BACKEND_STORE_URI by head setup"
  value       = module.rds.mlflow_backend_store_uri
  sensitive   = true
}

output "notes_db_uri" {
  description = "Published as NOTES_DB_URI by head setup"
  value       = module.rds.notes_db_uri
  sensitive   = true
}

output "rds_endpoint" {
  description = "RDS connection endpoint (host:port)"
  value       = module.rds.endpoint
}
