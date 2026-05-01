locals {
  ssh_public_key = file(pathexpand("~/.ssh/id_rsa.pub"))
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

data "aws_subnet" "router" {
  id = var.private_subnet_ids[0]
}

resource "aws_instance" "router" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnet.router.id
  vpc_security_group_ids      = [aws_security_group.router.id]
  associate_public_ip_address = false
  source_dest_check           = false

  user_data = templatefile("${path.module}/cloud-init.yaml", {
    tailscale_auth_key = var.tailscale_auth_key
    ssh_public_key     = local.ssh_public_key
    vpc_cidr           = var.vpc_cidr
  })

  # The router is stateless (only forwards packets), so replace it whenever
  # cloud-init changes -- in particular when the Tailscale auth key rotates.
  # Without this, terraform ignores user_data after first launch and the
  # router keeps its old registration; rotating the key would otherwise
  # require a manual `terraform taint`.
  user_data_replace_on_change = true

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  tags = {
    Project   = "robolab"
    Component = "tailscale-router"
    Name      = "robolab-tailscale-router"
  }
}
