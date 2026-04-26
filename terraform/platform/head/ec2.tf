locals {
  ssh_public_key = file(pathexpand("~/.ssh/id_rsa.pub"))
}

data "aws_ami" "ubuntu_arm64" {
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

data "aws_subnet" "primary" {
  id = data.aws_subnets.default.ids[0]
}

resource "aws_ebs_volume" "storage" {
  availability_zone = data.aws_subnet.primary.availability_zone
  size              = var.ebs_size_gb
  type              = "gp3"

  tags = {
    Project   = "robolab"
    Component = "head"
    Name      = "robolab-head-storage"
  }
}

resource "aws_instance" "head" {
  ami                         = data.aws_ami.ubuntu_arm64.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnet.primary.id
  vpc_security_group_ids      = [aws_security_group.head.id]
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/cloud-init.yaml", {
    tailscale_auth_key = var.tailscale_auth_key
    ssh_public_key     = local.ssh_public_key
    storage_volume_id  = aws_ebs_volume.storage.id
  })

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = {
    Project   = "robolab"
    Component = "head"
    Name      = "robolab-head"
  }
}

resource "aws_volume_attachment" "storage" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.storage.id
  instance_id = aws_instance.head.id
}
