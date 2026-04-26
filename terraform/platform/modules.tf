module "network" {
  source     = "./network"
  aws_region = var.aws_region
}

module "head" {
  source             = "./head"
  vpc_id             = module.network.vpc_id
  private_subnet_ids = module.network.private_subnet_ids
  tailscale_auth_key = var.tailscale_auth_key
}

module "s3" {
  source = "./s3"
}

module "rds" {
  source                 = "./rds"
  vpc_id                 = module.network.vpc_id
  private_subnet_ids     = module.network.private_subnet_ids
  head_security_group_id = module.head.security_group_id
}
