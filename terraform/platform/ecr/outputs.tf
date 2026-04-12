output "jobs_control_plane_repository_url" {
  description = "ECR repository URL for jobs-control-plane"
  value       = aws_ecr_repository.jobs_control_plane.repository_url
}

output "ray_head_repository_url" {
  description = "ECR repository URL for ray-head"
  value       = aws_ecr_repository.ray_head.repository_url
}

output "mlflow_repository_url" {
  description = "ECR repository URL for mlflow"
  value       = aws_ecr_repository.mlflow.repository_url
}
