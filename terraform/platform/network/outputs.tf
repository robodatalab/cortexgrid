output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "VPC CIDR"
  value       = aws_vpc.main.cidr_block
}

output "private_subnet_ids" {
  description = "Private subnet IDs (2 AZs, used by EC2 + RDS)"
  value       = aws_subnet.private[*].id
}

output "public_subnet_id" {
  description = "Public subnet ID (hosts NAT gateway)"
  value       = aws_subnet.public.id
}
