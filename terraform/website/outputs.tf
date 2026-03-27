output "s3_bucket_name" {
  description = "Name of the S3 bucket hosting the website"
  value       = aws_s3_bucket.website.id
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (needed for cache invalidation in CI/CD)"
  value       = aws_cloudfront_distribution.website.id
}

output "cloudfront_domain_name" {
  description = "CloudFront domain name"
  value       = aws_cloudfront_distribution.website.domain_name
}

output "website_url" {
  description = "Website URL"
  value       = "https://${var.domain_name}"
}

output "api_gateway_invoke_url" {
  description = "Auth API Gateway invoke URL"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "rds_endpoint" {
  description = "Auth RDS endpoint — connect here to run schema.sql after first deploy"
  value       = "${aws_db_instance.auth.address}:${aws_db_instance.auth.port}"
}
