variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2"
}

variable "github_repos" {
  description = "GitHub repos allowed to assume the CI role (owner/repo format)"
  type        = list(string)
  default     = ["robodatalab/cortexgrid"]
}
