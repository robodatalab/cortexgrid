# Platform

VPC + EC2 head + Tailscale subnet router + RDS + S3 + secrets/IAM.

## Problem

Every workload runs inside one VPC on private subnets. Reachability from
laptops/CI is via Tailscale only -- nothing is public. RDS holds metadata for
multiple databases (`mlflow`, `notes`); S3 holds artifacts; the head EC2 runs
k3s; an EKS cluster may slot in later, in the same VPC.

## Modules

- **network/** -- VPC, two private subnets, public subnet for NAT.
- **head/** -- single EC2 in private subnet, runs k3s, joins tailnet as a node.
- **tailscale-router/** -- single small EC2 in private subnet, advertises the
  whole VPC CIDR into the tailnet so any Tailscale device reaches every
  in-VPC IP (EC2, RDS, future EKS) by private IP.
- **rds/** -- single Postgres instance. Hosts `mlflow` (created by RDS itself)
  and `notes` (created by terraform via psql against RDS). Publishes
  `MLFLOW_BACKEND_STORE_URI` and `NOTES_DB_URI` to AWS Secrets Manager.
- **s3/** -- artifact bucket.
- **secrets/** -- separate state, IAM only (DGX user, GitHub Actions OIDC role).

## Bootstrap (cold start)

The notes-database provisioner runs `psql` against RDS from your laptop, which
requires the Tailscale subnet router to be up *and* its advertised route
approved. So a fresh apply is two phases:

1. `terraform apply -target=module.network -target=module.head -target=module.tailscale_router -target=module.s3`
2. In the [Tailscale admin](https://login.tailscale.com/admin/machines), find
   `robolab-tailscale-router` and approve the advertised subnet route (the VPC
   CIDR, default `10.0.0.0/16`). One-time, ever.
3. `terraform apply` -- completes the RDS module including the `notes`
   database, schema, and `NOTES_DB_URI` secret.

Subsequent applies are a single `terraform apply`.

## Dependencies

- `~/.ssh/id_rsa.pub` exists -- baked into EC2 cloud-init for SSH.
- `psql` on the apply machine -- used by the notes-database provisioner.
- `TF_VAR_tailscale_auth_key` set in env -- one-shot reusable Tailscale auth key.
