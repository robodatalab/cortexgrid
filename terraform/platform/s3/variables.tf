variable "bucket_name" {
  description = "S3 bucket name. Must be globally unique. S3 has no upfront capacity, pay for storage actually used."
  type        = string
  default     = "robolab-data"
}

variable "aws_region" {
  description = "AWS region. Used to compose the regional S3 endpoint URL surfaced in SM."
  type        = string
}
