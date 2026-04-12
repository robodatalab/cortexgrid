variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "github_repos" {
  description = "GitHub repos allowed to assume the CI role (owner/repo format)"
  type        = list(string)
  default     = ["paksas/robolabwebsite", "paksas/robolab-infra"]
}
