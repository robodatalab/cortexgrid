variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "domain_name" {
  type        = string
  description = "Route 53 hosted zone name (e.g. robodatalab.com)"
}

variable "tailnet" {
  type        = string
  description = "Tailscale tailnet suffix without leading dot (e.g. tailaa75f1.ts.net)"
}

variable "hostnames" {
  type        = list(string)
  description = "List of subdomain labels to register. Each becomes <label>.<domain_name> CNAME -> <label>.<tailnet>."
}
