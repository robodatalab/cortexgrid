# RoboLab Infrastructure

Cloud infrastructure, ML compute, and deployment orchestration for RoboLab. Cloud resources run on AWS (`eu-west-2`, except the marketing site which stays in `us-east-1` because CloudFront requires `us-east-1` ACM) and are provisioned with Terraform. ML compute runs on a DGX Spark worker that joins the cluster over Tailscale.

## Architecture

```
  AWS
  +--------------------------------------------------+
  |                                                  |
  |  Route53 --> CloudFront --> S3 (robodatalab.com) |
  |                  |                               |
  |            API Gateway --> Lambda (auth)          |
  |                              |                   |
  |                     VPC [ RDS + NAT ]            |
  |                                                  |
  |  Secrets Manager (robolab/auth/*, robolab/infra/*) |
  |       ^                                          |
  |       |  OIDC                                    |
  |  GitHub Actions                                  |
  +--------------------------------------------------+

  Tailscale Network
  +-------------+    +---------------------------+    +---------------------------+
  |  MacBook    |--->|  EC2 head (k3s server)    |<-->|  DGX Spark (k3s worker)   |
  |  your code  |    |    role=head              |    |    role=worker            |
  |  cortexflow |    |    argocd, mlflow,        |    |    ray-head, ray-worker   |
  |             |    |    cortexflow-ui,         |    |    (GPU workloads)        |
  |             |    |    jobs-control-plane     |    |                           |
  +-------------+    +---------------------------+    +---------------------------+
                            |               |
                       AWS RDS         AWS S3 (data + mlflow artifacts)
```

The "head" runs on AWS EC2 by default. On-prem head deployment is supported in principle
(K3sServer auto-detects via `/sys/class/dmi/id/sys_vendor`) but the on-prem profile is
currently broken at the mlflow layer — see notes below.

### Folder layout

| Folder | Purpose |
|--------|---------|
| `terraform/website/` | Marketing site + investor auth infrastructure |
| `terraform/platform/secrets/` | Centralized secrets management (AWS Secrets Manager + IAM) |
| `terraform/platform/network/` | VPC, public/private subnets, NAT, S3 Gateway endpoint |
| `terraform/platform/head/` | EC2 + EBS that hosts the k3s head and platform services |
| `terraform/platform/s3/` | Data + mlflow-artifacts bucket and IAM grant on `robolab-dgx` |
| `terraform/platform/rds/` | Postgres for mlflow backend store; password generated, pushed to SM |
| `k8s/` | Argo CD GitOps platform — bootstrap manifest, Argo Applications, workload manifests, one-shot seed scripts |
| `cortexflow/` | Python library for ML code to reach Ray/MLflow/S3 |
| `lambda/auth/` | Auth Lambda source (deployed by `terraform/website/`) |

## cortexflow

`cortexflow` is a Python library that connects your ML code to the deployed infrastructure. It wraps Ray, MLflow, and S3/MinIO so your training scripts don't need to know about URLs, credentials, or service endpoints.

See [cortexflow/README.md](cortexflow/README.md) for full reference

### Setup

**Prerequisites:** Mac on the Tailscale network. Mac has `uv`, `kubectl`, `terraform`, and AWS credentials with access to `robolab/*` secrets. A `.env` file at the repo root with `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `GH_TOKEN`, `TAILSCALE_AUTH_KEY` (ephemeral, reusable).

**1. Provision the AWS head infrastructure** (terraform — VPC, EC2, S3, RDS, all in one shot):

```bash
make head-aws-apply
```

This runs `terraform apply` on `network → head → s3 → rds` in sequence and writes `Host robolab-aws <ip>` into `~/.ssh/config`. The EC2 boots, joins your tailnet as `robolab-head`, and mounts the EBS volume at `/storage`.

**2. Seed the head** (k3s + ArgoCD bootstrap):

```bash
make head-setup IP=<robolab-head-tailscale-ip> STORAGE_PATH=/storage
```

**3. Seed a worker** (DGX joins as GPU worker):

```bash
make worker-setup IP=<dgx-tailscale-ip>
```

**Teardown:**

```bash
make node-teardown IP=<tailscale-ip>      # k3s teardown on a single node
make head-aws-destroy                     # destroy AWS infra (rds → s3 → head → network)
```

`head-setup` installs k3s, stages [k8s/argocd.yaml](k8s/argocd.yaml), publishes the k3s token + service URLs to AWS Secrets Manager under `robolab/infra/*`, merges the kubeconfig into `~/.kube/config` as context `robolab`, and labels the node `role=head`. Argo CD then reconciles everything under [k8s/argo-deployments/](k8s/argo-deployments/) from `main`. Topology is recorded in [infra-config.yaml](infra-config.yaml) at the repo root.

### Secrets management

One namespace, backed by AWS Secrets Manager:

- **`robolab/infra/*`** → written by `setup-node` from `.env`; read at cluster level by [External Secrets Operator](k8s/argo-deployments/secrets/) (which materializes k8s Secrets for GHCR pull, repo clone creds, AWS access) and at application level by [`cortexflow.secrets`](cortexflow/secrets.py).

## Website infrastructure

### `terraform/website/` — Marketing website + investor auth

Serves the marketing site at **robodatalab.com** and the investor authentication backend.
Source: `robolabwebsite` repo.

| Resource | Purpose |
|----------|---------|
| S3 bucket | Hosts built static assets (private, OAC-gated) |
| CloudFront | CDN; routes `/api/*` to API Gateway, `/*` to S3 |
| ACM certificate | TLS for `robodatalab.com` + `www`, DNS-validated |
| Route53 records | A-record aliases for apex and `www` |
| OIDC provider + role | GitHub Actions deploys via `AssumeRoleWithWebIdentity` |
| VPC + NAT gateway | Isolates RDS; Lambda uses NAT for outbound SES calls |
| RDS PostgreSQL | Investor whitelist + OTP codes (`db.t4g.micro`) |
| Lambda (Node 22) | Auth API — OTP email flow, JWT issuance, admin approval |
| API Gateway v2 | HTTPS endpoint for the Lambda |
| SES email identity | Sends OTP and approval emails from `noreply@robodatalab.com` |

State backend: `s3://robolab-terraform-state/website/terraform.tfstate`

**First-deploy order:**
1. `terraform apply` (with `api_gateway_domain = ""`)
2. Copy `api_gateway_invoke_url` output -> set `api_gateway_domain` in `terraform.tfvars`
3. `terraform apply` again (wires up CloudFront `/api/*` behavior)
4. `psql -h <rds_endpoint> ... -f ../../lambda/auth/schema.sql` to create tables
5. Verify SES sender in AWS Console; request production access to lift sandbox

### `terraform/platform/network/` — VPC + private subnets

| Resource | Purpose |
|----------|---------|
| VPC `10.0.0.0/16` | Project VPC with DNS hostnames + DNS support enabled |
| 1 public subnet | Hosts the NAT gateway only |
| 2 private subnets | EC2 head lands in `private[0]`; RDS subnet group needs both AZs |
| IGW + NAT gateway | Outbound internet for the private subnets (Tailscale auth, GHCR pulls) |
| S3 Gateway VPC endpoint | Free S3 access from private subnets — bypasses NAT, no egress charge |

State backend: `s3://robolab-terraform-state/platform/network/terraform.tfstate`

### `terraform/platform/head/` — k3s head EC2

Provisions the EC2 instance that runs the k3s control plane, Argo CD, and platform services in `eu-west-2`. The DGX Spark joins as a worker over Tailscale.

| Resource | Purpose |
|----------|---------|
| EC2 (`t3.large`, Ubuntu 24.04 amd64, in private subnet, no public IP) | Hosts the k3s server + workloads pinned to `role=head` |
| EBS gp3 (100 GB default) | Mounted at `/storage`; backs k3s local-path PVCs. Online-resizable via `aws ec2 modify-volume`. |
| Security group | Egress-all only — no inbound. SSH and k3s API access are over Tailscale, which uses outbound DERP relays for inbound peer connections. |
| Cloud-init | Adds your `~/.ssh/id_rsa.pub` to the `ubuntu` user, installs Tailscale (joins tailnet via auth key from `.env`), formats and mounts the EBS volume. |

VPC + private subnets are looked up by tag (`Project=robolab`, `Type=private`) so this module has no `terraform_remote_state` dependency on `network/`.

State backend: `s3://robolab-terraform-state/platform/head/terraform.tfstate`

### `terraform/platform/s3/` — Data + mlflow-artifacts bucket

| Resource | Purpose |
|----------|---------|
| S3 bucket `robolab-data` | AES256, public access blocked. Used for cortexflow data uploads and mlflow artifacts under `mlflow-artifacts/`. |
| IAM user policy | Attaches read/write to the existing `robolab-dgx` IAM user (also reused by ESO/in-cluster boto3). |
| SM `robolab/infra/S3_BUCKET_NAME` | Bucket name surfaced for ESO → `aws-creds` Secret → all consumer pods. |

State backend: `s3://robolab-terraform-state/platform/s3/terraform.tfstate`

### `terraform/platform/rds/` — Postgres for mlflow backend store

| Resource | Purpose |
|----------|---------|
| `db.t4g.micro` Postgres 16 | Single-AZ, encrypted gp3, ingress only from the head's SG. |
| Random master password | 32 chars, stored in `robolab/infra/RDS_PASSWORD`. |
| SM entries | `RDS_HOST`, `RDS_PORT`, `RDS_USERNAME`, `RDS_DB_NAME`, `RDS_PASSWORD`. ESO templates `MLFLOW_BACKEND_STORE_URI` from these. |

State backend: `s3://robolab-terraform-state/platform/rds/terraform.tfstate`

### Service discovery

In-cluster service-to-service calls use cluster DNS (`mlflow.mlflow.svc.cluster.local:5000`,
`ray-head.ray.svc.cluster.local:8265`) — hardcoded in deployment manifests.

Off-cluster URLs and shared infra coordinates flow through AWS Secrets Manager:

- `head-setup` writes the head's Tailscale IP and computed service URLs (`MLFLOW_TRACKING_URI`, `RAY_JOB_SERVER_URI`) to SM.
- Terraform writes RDS coordinates and S3 bucket name to SM.
- ESO syncs all `robolab/infra/*` into k8s Secrets; Reflector mirrors `aws-creds` (with `S3_BUCKET_NAME`) to every workload namespace.
- `cortexflow.infra` reads URLs from env first (in-cluster) or falls back to SM (laptop dev).

### `terraform/platform/secrets/` — Centralized secrets

IAM users, roles, and OIDC configuration for accessing AWS Secrets Manager. Secret values are written by `setup-node` from `.env` (see [k8s/seed/operators/env_secrets.py](k8s/seed/operators/env_secrets.py)), not Terraform.

### Security posture

Both CloudFront distributions enforce:
- HTTPS-only (HTTP -> HTTPS redirect)
- TLS 1.2+
- HSTS with preload (1 year max-age, includeSubDomains)
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- S3 buckets are fully private (all public access blocked, CloudFront OAC only)

### CI/CD

The secrets module provisions a GitHub Actions OIDC integration:
- An `aws_iam_openid_connect_provider` trusts `token.actions.githubusercontent.com`
- The `robolab-github-actions` IAM role is assumable from configured repos
- Permissions: `secretsmanager:GetSecretValue` for all `robolab/*` secrets

## Related repos

| Repo | Description |
|------|-------------|
| `robolab-platform` | Platform frontend and backend |
| `robolab-sims-unity` | Unity simulation projects |
| `robolab-sims-unreal` | Unreal Engine simulation projects |
| `robolabwebsite` | Marketing website source |
| `model-gateway` | LLM provider abstraction layer |
| `model-training` | Training facilities (SFT, LoRA) |

## Prerequisites

- Terraform >= 1.5
- AWS CLI configured with appropriate credentials
- [uv](https://docs.astral.sh/uv/) for Python dependency management
- Tailscale on Mac and DGX (for ML compute)

## Further documentation

- [k8s/README.md](k8s/README.md) — GitOps overview + bootstrap FAQ
- [k8s/argo-deployments/](k8s/argo-deployments/) — one-pager README per platform component (Ray, MLflow, monitoring, secrets, NVIDIA device plugin, jobs control plane; on-prem-only: MinIO, Postgres)
- [cortexflow/README.md](cortexflow/README.md) — Python library reference
