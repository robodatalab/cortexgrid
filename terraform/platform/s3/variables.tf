variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2"
}

variable "bucket_name" {
  description = "S3 bucket name. Must be globally unique. S3 has no upfront capacity — pay for storage actually used."
  type        = string
  default     = "robolab-data"
}
