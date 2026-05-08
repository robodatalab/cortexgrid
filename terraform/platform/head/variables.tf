variable "vpc_id" {
  description = "VPC ID — supplied by the network module."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs from the network module. The EC2 lands in private_subnet_ids[0]."
  type        = list(string)
}

variable "nodes" {
  description = "Members of the head deployment set. Map key is a stable name used in tags and the Tailscale hostname suffix; value sizes the EC2 and its attached EBS. amd64 because most workload images are not yet multi-arch (only ray is). t3.xlarge sized for argocd + mlflow + cortexflow-ui + jobs-control-plane + prometheus sharing the box; t3.large saturated under reconciliation spikes."
  type = map(object({
    instance_type = string
    ebs_size_gb   = number
  }))
  default = {
    primary = {
      instance_type = "t3.xlarge"
      ebs_size_gb   = 100
    }
  }
}

variable "tailscale_auth_key" {
  description = "One-shot Tailscale auth key. Pass via TF_VAR_tailscale_auth_key from .env at apply-time; consumed once by cloud-init at first boot."
  type        = string
  sensitive   = true
}
