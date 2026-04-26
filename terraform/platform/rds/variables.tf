variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2"
}

variable "instance_class" {
  description = "RDS instance class. db.t4g.micro is single-vCPU/1GB-RAM, fits dev workloads."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage_gb" {
  description = "RDS gp3 storage. Online-resizable later."
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Initial database name. mlflow is the only current consumer; additional databases on the same instance can be created later via the postgres provider or app-managed migrations."
  type        = string
  default     = "mlflow"
}

variable "engine_version" {
  description = "PostgreSQL major.minor version pinned for reproducibility."
  type        = string
  default     = "16.4"
}
