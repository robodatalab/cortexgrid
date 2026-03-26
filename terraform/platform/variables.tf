variable "aws_region" {
  description = "AWS region for the S3 bucket"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "robolab-platform"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}
