resource "aws_security_group" "router" {
  name        = "robolab-tailscale-router"
  description = "Tailscale subnet router -- outbound only; inbound peer connections via DERP relays"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project   = "robolab"
    Component = "tailscale-router"
  }
}
