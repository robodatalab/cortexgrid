resource "aws_db_subnet_group" "main" {
  name        = "robolab-rds"
  description = "Private subnets for the robolab RDS instance"
  subnet_ids  = var.private_subnet_ids

  tags = {
    Project   = "robolab"
    Component = "rds"
  }
}

resource "aws_security_group" "rds" {
  name        = "robolab-rds"
  description = "Allow Postgres from the k3s head and the Tailscale subnet router"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres from k3s head"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.head_security_group_id]
  }

  ingress {
    description     = "Postgres from Tailscale subnet router (laptops/CI via tailnet)"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.router_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project   = "robolab"
    Component = "rds"
  }
}
