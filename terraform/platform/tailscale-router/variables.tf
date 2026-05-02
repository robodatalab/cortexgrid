variable "vpc_id" {
  description = "VPC ID -- supplied by the network module."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs from the network module. The router lands in private_subnet_ids[0]."
  type        = list(string)
}

variable "vpc_cidr" {
  description = "VPC CIDR advertised into the tailnet. Must match the VPC's CIDR exactly so all in-VPC IPs (EC2, RDS, future EKS) are reachable from Tailscale clients."
  type        = string
}

variable "tailscale_auth_key" {
  description = "One-shot Tailscale auth key. Pass via TF_VAR_tailscale_auth_key from .env at apply-time; consumed once by cloud-init at first boot."
  type        = string
  sensitive   = true
}

variable "instance_type" {
  description = "EC2 instance type for the subnet router. t4g.nano is sufficient -- the router only forwards packets, no workloads."
  type        = string
  default     = "t4g.nano"
}
