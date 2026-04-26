variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2"
}

variable "instance_type" {
  description = "EC2 instance type for the k3s head. amd64 because most workload images are not yet multi-arch — only ray is."
  type        = string
  default     = "t3.large"
}

variable "ebs_size_gb" {
  description = "Size of the gp3 EBS volume mounted at the head's storage_path. Online-resizable."
  type        = number
  default     = 100
}

variable "tailscale_auth_key" {
  description = "One-shot Tailscale auth key. Pass via TF_VAR_tailscale_auth_key from .env at apply-time; consumed once by cloud-init at first boot."
  type        = string
  sensitive   = true
}
