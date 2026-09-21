# Platform

VPC + EC2 head + Tailscale subnet router + RDS + S3.

## Problem

Every workload runs inside one VPC on private subnets. Reachability from
laptops/CI is via Tailscale only -- nothing is public. RDS holds metadata for
multiple databases (`mlflow`, `notes`); S3 holds artifacts; the head EC2 runs
k3s; an EKS cluster may slot in later, in the same VPC.

## Modules

- **network/** -- VPC, two private subnets, public subnet for NAT.
- **head/** -- single EC2 in private subnet, runs k3s, joins tailnet as a node.
  Also creates the `robolab-dgx` IAM user whose key the cluster uses for S3.
- **tailscale-router/** -- single small EC2 in private subnet, advertises the
  whole VPC CIDR into the tailnet so any Tailscale device reaches every
  in-VPC IP (EC2, RDS, future EKS) by private IP.
- **rds/** -- single Postgres instance. Hosts `mlflow` (created by RDS itself)
  and `notes` (created by terraform via psql against RDS). Exposes
  `MLFLOW_BACKEND_STORE_URI` and `NOTES_DB_URI` as outputs, which head setup
  publishes to the head secrets store.
- **s3/** -- artifact bucket, and the S3 policy on `robolab-dgx`.

## Bootstrap

`./cg add head --aws` applies the stack in a single pass. The notes-database provisioner has a
10-minute retry loop that absorbs router boot + Tailscale route propagation,
so cold-start and incremental applies behave the same.

This relies on two pieces of one-time Tailscale tenant configuration:

1. **Auth key** ([Tailscale admin -> Settings -> Keys](https://login.tailscale.com/admin/settings/keys)):
   Reusable, non-Ephemeral. Put it in `.env.head` as `TAILSCALE_AUTH_KEY`. The same
   key is consumed by both the head and the subnet router (cloud-init runs on
   each).

2. **ACL** ([Tailscale admin -> Access Controls](https://login.tailscale.com/admin/acls)):
   Routes advertised by admin-owned devices must be auto-approved. Add to your
   tailnet policy file:

   ```jsonc
   "autoApprovers": {
     "routes": {
       "10.0.0.0/16": ["autogroup:admin"]
     }
   }
   ```

   With this in place, the router's advertised `10.0.0.0/16` is approved the
   moment it joins the tailnet -- no manual click needed and no tagging
   required.

## Dependencies

- `~/.ssh/id_rsa.pub` exists -- baked into EC2 cloud-init for SSH.
- `psql` on the apply machine -- used by the notes-database provisioner.
- `TF_VAR_tailscale_auth_key` set in env -- reusable, non-Ephemeral auth key
  (see above).
