variable "vpc_id" {
  description = "VPC ID — supplied by the network module."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs from the network module. RDS subnet group requires >=2 AZs even for single-AZ instances."
  type        = list(string)
}

variable "head_security_group_id" {
  description = "Security group ID of the k3s head -- Postgres ingress is allowed from it."
  type        = string
}

variable "router_security_group_id" {
  description = "Security group ID of the Tailscale subnet router -- Postgres ingress is allowed from it so Tailscale clients (laptops, CI) reach RDS via SNAT."
  type        = string
}

variable "router_instance_id" {
  description = "Instance ID of the Tailscale subnet router -- referenced in notes provisioner triggers so terraform doesn't run psql until the router exists."
  type        = string
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
  description = "Initial database name, used by mlflow. The notes and cortexgrid databases on the same instance are created by notes.tf and cortexgrid.tf."
  type        = string
  default     = "mlflow"
}

variable "engine_version" {
  description = "PostgreSQL major.minor version pinned for reproducibility. AWS deprecates older minor versions periodically; bump as needed (check `aws rds describe-db-engine-versions --engine postgres`)."
  type        = string
  default     = "16.13"
}
