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

output "rds_endpoint" {
  description = "RDS connection endpoint (host:port)"
  value       = module.rds.endpoint
}
