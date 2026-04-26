data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "head" {
  name        = "robolab-head"
  description = "Tailscale-only access for the k3s head EC2"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Tailscale UDP for direct peer-to-peer; falls back to DERP if blocked"
    from_port   = 41641
    to_port     = 41641
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

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
