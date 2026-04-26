output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.head.id
}

output "ebs_volume_id" {
  description = "EBS volume ID for the storage_path mount; use with `aws ec2 modify-volume` to resize"
  value       = aws_ebs_volume.storage.id
}

output "security_group_id" {
  description = "Security group ID — consumed by rds module to allow ingress from the head."
  value       = aws_security_group.head.id
}
