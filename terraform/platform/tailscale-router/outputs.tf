output "instance_id" {
  description = "EC2 instance ID of the Tailscale subnet router"
  value       = aws_instance.router.id
}

output "security_group_id" {
  description = "Security group ID -- consumed by RDS (and other in-VPC services) to allow ingress from Tailscale clients routed via this node."
  value       = aws_security_group.router.id
}
