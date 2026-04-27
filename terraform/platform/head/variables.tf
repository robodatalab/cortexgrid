variable "vpc_id" {
  description = "VPC ID — supplied by the network module."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs from the network module. The EC2 lands in private_subnet_ids[0]."
  type        = list(string)
}

variable "instance_type" {
  description = "EC2 instance type for the k3s head. amd64 because most workload images are not yet multi-arch (only ray is). Sized for argocd + mlflow + cortexflow-ui + jobs-control-plane + prometheus stack sharing the box; t3.large saturated under reconciliation spikes."
  type        = string
  default     = "t3.xlarge"
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
