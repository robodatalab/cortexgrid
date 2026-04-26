output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.head.id
}

output "public_dns" {
  description = "Public DNS — first-boot debugging only; real access is over Tailscale"
  value       = aws_instance.head.public_dns
}

output "ebs_volume_id" {
  description = "EBS volume ID for the storage_path mount; use with `aws ec2 modify-volume` to resize"
  value       = aws_ebs_volume.storage.id
}
