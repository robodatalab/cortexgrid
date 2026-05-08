locals {
  ssh_public_key = file(pathexpand("~/.ssh/id_rsa.pub"))
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

data "aws_subnet" "head" {
  id = var.private_subnet_ids[0]
}

resource "aws_ebs_volume" "storage" {
  for_each          = var.nodes
  availability_zone = data.aws_subnet.head.availability_zone
  size              = each.value.ebs_size_gb
  type              = "gp3"

  tags = {
    Project   = "robolab"
    Component = "head"
    Name      = "robolab-head-${each.key}-storage"
  }
}

resource "aws_instance" "head" {
  for_each               = var.nodes
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = each.value.instance_type
  subnet_id              = data.aws_subnet.head.id
  vpc_security_group_ids = [aws_security_group.head.id]
  # Private subnet: no public IP. Tailscale joins via outbound NAT and uses
  # DERP relays for inbound peer connections.
  associate_public_ip_address = false

  user_data = templatefile("${path.module}/cloud-init.yaml", {
    tailscale_auth_key = var.tailscale_auth_key
    ssh_public_key     = local.ssh_public_key
    storage_volume_id  = aws_ebs_volume.storage[each.key].id
    hostname           = each.key == "primary" ? "robolab-head" : "robolab-head-${each.key}"
  })

  root_block_device {
    volume_size = 300
    volume_type = "gp3"
  }

  tags = {
    Project   = "robolab"
    Component = "head"
    Name      = each.key == "primary" ? "robolab-head" : "robolab-head-${each.key}"
  }
}

resource "aws_volume_attachment" "storage" {
  for_each    = var.nodes
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.storage[each.key].id
  instance_id = aws_instance.head[each.key].id
}

# State migration: existing single-instance resources move under the "primary"
# for_each key, so terraform plan does not destroy/recreate the live head.
moved {
  from = aws_ebs_volume.storage
  to   = aws_ebs_volume.storage["primary"]
}

moved {
  from = aws_instance.head
  to   = aws_instance.head["primary"]
}

moved {
  from = aws_volume_attachment.storage
  to   = aws_volume_attachment.storage["primary"]
}
