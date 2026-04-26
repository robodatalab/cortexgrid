data "aws_vpc" "robolab" {
  filter {
    name   = "tag:Project"
    values = ["robolab"]
  }
  filter {
    name   = "tag:Name"
    values = ["robolab"]
  }
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.robolab.id]
  }
  filter {
    name   = "tag:Type"
    values = ["private"]
  }
}

# The k3s head's security group, created by terraform/platform/head. Looked up
# by name so the two modules don't need a remote-state dependency.
data "aws_security_group" "head" {
  name = "robolab-head"
}

resource "aws_db_subnet_group" "main" {
  name        = "robolab-rds"
  description = "Private subnets for the robolab RDS instance"
  subnet_ids  = data.aws_subnets.private.ids

  tags = {
    Project   = "robolab"
    Component = "rds"
  }
}

resource "aws_security_group" "rds" {
  name        = "robolab-rds"
  description = "Allow Postgres only from the k3s head"
  vpc_id      = data.aws_vpc.robolab.id

  ingress {
    description     = "Postgres from k3s head"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [data.aws_security_group.head.id]
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
