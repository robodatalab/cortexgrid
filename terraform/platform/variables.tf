variable "aws_region" {
  description = "AWS region for the platform stack."
  type        = string
  default     = "eu-west-2"
}

variable "tailscale_auth_key" {
  description = "One-shot Tailscale auth key. Pass via TF_VAR_tailscale_auth_key from .env at apply-time; consumed once by cloud-init at first boot."
  type        = string
  sensitive   = true
}
