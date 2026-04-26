variable "aws_region" {
  description = "AWS region — used to construct the S3 Gateway endpoint service name."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}
