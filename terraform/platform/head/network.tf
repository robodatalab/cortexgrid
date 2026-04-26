# Look up the VPC + private subnets created by terraform/platform/network via
# tags. Tag-based lookups avoid coupling state files (no terraform_remote_state)
# while still enforcing the dependency: head-aws-apply must run network first.

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

resource "aws_security_group" "head" {
  name        = "robolab-head"
  description = "k3s head EC2 — outbound only; access is via Tailscale through DERP relays"
  vpc_id      = data.aws_vpc.robolab.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project   = "robolab"
    Component = "head"
  }
}
