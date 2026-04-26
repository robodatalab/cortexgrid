output "bucket_name" {
  description = "S3 bucket name"
  value       = aws_s3_bucket.main.id
}

output "bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.main.arn
}

output "bucket_regional_domain_name" {
  description = "Regional domain (use as endpoint when constructing s3:// URLs)"
  value       = aws_s3_bucket.main.bucket_regional_domain_name
}
