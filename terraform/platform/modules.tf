module "network" {
  source     = "./network"
  aws_region = var.aws_region
}

module "head" {
  source             = "./head"
  vpc_id             = module.network.vpc_id
  private_subnet_ids = module.network.private_subnet_ids
  tailscale_auth_key = var.tailscale_auth_key

  # Two members on AWS: the original "primary" carries argocd, mlflow,
  # prometheus, etc.; the "loki" peer is dedicated to the Loki single-binary
  # so log ingestion does not contend with platform services. The k8s
  # scheduler distributes role=head workloads across both members.
  nodes = {
    primary = { instance_type = "t3.xlarge", ebs_size_gb = 100 }
    loki    = { instance_type = "t3.xlarge", ebs_size_gb = 100 }
  }
}

module "tailscale_router" {
  source             = "./tailscale-router"
  vpc_id             = module.network.vpc_id
  private_subnet_ids = module.network.private_subnet_ids
  vpc_cidr           = module.network.vpc_cidr
  tailscale_auth_key = var.tailscale_auth_key
}

module "s3" {
  source     = "./s3"
  aws_region = var.aws_region
}

module "rds" {
  source                   = "./rds"
  vpc_id                   = module.network.vpc_id
  private_subnet_ids       = module.network.private_subnet_ids
  head_security_group_id   = module.head.security_group_id
  router_security_group_id = module.tailscale_router.security_group_id
  router_instance_id       = module.tailscale_router.instance_id
}
