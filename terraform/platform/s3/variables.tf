variable "bucket_name" {
  description = "S3 bucket name. Must be globally unique. S3 has no upfront capacity — pay for storage actually used."
  type        = string
  default     = "robolab-data"
}
