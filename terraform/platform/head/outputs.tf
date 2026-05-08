output "instance_ids" {
  description = "Map of node name to EC2 instance ID for every member of the head set."
  value       = { for k, v in aws_instance.head : k => v.id }
}

output "ebs_volume_ids" {
  description = "Map of node name to EBS volume ID for the storage_path mount; use with `aws ec2 modify-volume` to resize."
  value       = { for k, v in aws_ebs_volume.storage : k => v.id }
}

output "security_group_id" {
  description = "Security group ID — consumed by rds module to allow ingress from the head."
  value       = aws_security_group.head.id
}
