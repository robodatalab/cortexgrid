output "head_instance_id" {
  description = "EC2 instance ID of the k3s head"
  value       = module.head.instance_id
}

output "head_ebs_volume_id" {
  description = "EBS volume ID for /storage; use with `aws ec2 modify-volume` to resize"
  value       = module.head.ebs_volume_id
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
