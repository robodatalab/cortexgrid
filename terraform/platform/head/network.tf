resource "aws_security_group" "head" {
  name        = "robolab-head"
  description = "k3s head EC2 - outbound only; access is via Tailscale through DERP relays"
  vpc_id      = var.vpc_id

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
