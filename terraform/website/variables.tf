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

variable "api_gateway_domain" {
  description = "Domain of the auth API Gateway (without https://). When set, adds an /api/* CloudFront behavior routing to API Gateway. Leave empty to skip."
  type        = string
  default     = ""
}

# ── Investor auth ─────────────────────────────────────────────────────────────

variable "admin_email" {
  description = "Email address that receives investor access-request notifications"
  type        = string
  default     = ""
}

variable "from_email" {
  description = "SES-verified sender address (e.g. noreply@robodatalab.com)"
  type        = string
  default     = ""
}

variable "db_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "robolab_auth"
}

variable "db_username" {
  description = "PostgreSQL master username"
  type        = string
  default     = "robolab_auth"
}

variable "db_password" {
  description = "PostgreSQL master password (sensitive — pass via TF_VAR_db_password)"
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "HS256 signing secret for JWTs, min 32 chars (sensitive — pass via TF_VAR_jwt_secret)"
  type        = string
  sensitive   = true
}
