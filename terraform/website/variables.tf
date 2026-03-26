variable "aws_region" {
  description = "AWS region for the S3 bucket"
  type        = string
  default     = "us-east-1"
}

variable "domain_name" {
  description = "Primary domain name for the website (e.g. robolab.io)"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "github_repo" {
  description = "GitHub repository in owner/repo format (for OIDC trust)"
  type        = string
}
