output "s3_bucket_name" {
  description = "Name of the S3 bucket hosting the platform frontend"
  value       = aws_s3_bucket.frontend.id
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (needed for cache invalidation in CI/CD)"
  value       = aws_cloudfront_distribution.frontend.id
}

output "cloudfront_domain_name" {
  description = "CloudFront domain name"
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "platform_url" {
  description = "Platform URL (CloudFront domain until custom domain is configured)"
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}
