# RoboLab Infrastructure

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
  |  cortexgrid |    |    argocd, mlflow,        |    |    ray-head, ray-worker   |
  |             |    |    cortexgrid-ui,         |    |    (GPU workloads)        |
  |             |    |    jobs-control-plane     |    |                           |
  +-------------+    +---------------------------+    +---------------------------+
                            |               |
                       AWS RDS         AWS S3 (data + mlflow artifacts)
```

The "head" runs on AWS EC2 by default. On-prem head deployment (e.g. on the DGX itself,
or a ThinkStation, with in-cluster MinIO + Postgres replacing real S3 + RDS) is also
supported. K3sServer auto-detects the profile from `/sys/class/dmi/id/sys_vendor` at
seed time; downstream operators read it via `deps["profile"]`. See "Service discovery"
below for how the same workload manifests work in both profiles.

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
| `cortexgrid/` | Python library for ML code to reach Ray/MLflow/S3 |
| `lambda/auth/` | Auth Lambda source (deployed by `terraform/website/`) |

## cortexgrid

`cortexgrid` is a Python library that connects your ML code to the deployed infrastructure. It wraps Ray, MLflow, and S3/MinIO so your training scripts don't need to know about URLs, credentials, or service endpoints.

See [cortexgrid/README.md](cortexgrid/README.md) for full reference

### Setup

**Prerequisites:** Mac on the Tailscale network. Mac has `uv`, `kubectl`, `terraform`, and AWS credentials with access to `robolab/*` secrets. A `.env` file at the repo root with `SM_ACCESS_KEY_ID`, `SM_SECRET_ACCESS_KEY`, `SM_REGION` (the real-AWS keys used by seed to write into AWS Secrets Manager), `GH_TOKEN`, `TAILSCALE_AUTH_KEY` (ephemeral, reusable).

**1. Provision the AWS head infrastructure** (terraform — VPC, EC2, S3, RDS, all in one shot):

```bash
make head-aws-apply
```

This runs a single `terraform apply` against the root composition at [terraform/platform/](../terraform/platform/), which provisions network → head → s3 → rds in dependency order, then writes `Host robolab-aws <ip>` into `~/.ssh/config`. The EC2 boots, joins your tailnet as `robolab-head`, and mounts the EBS volume at `/storage`.

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
make head-aws-destroy                     # single terraform destroy of the platform stack
```

`head-setup` installs k3s, stages [k8s/argocd.yaml](../k8s/argocd.yaml), publishes the k3s token + service URLs to AWS Secrets Manager under `robolab/infra/*`, merges the kubeconfig into `~/.kube/config` as context `robolab`, and labels the node `role=head`. Argo CD then reconciles everything under [k8s/argo_deployments/](../k8s/argo_deployments/) from `main`. Topology is recorded in [infra-config.yaml](../infra-config.yaml) at the repo root.

### Secrets management

One namespace, backed by AWS Secrets Manager:

- **`robolab/infra/*`** → written by three producers depending on the entry: `EnvSecrets` (every `.env` key, including `SM_*`), `PlatformConfig` (mirrors `SM_*` into `ROUTE53_*` on both profiles and `S3_*` on AWS), and `terraform/platform/{rds,s3,secrets}` (AWS-managed coordinates: `S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET_NAME`, `MLFLOW_BACKEND_STORE_URI`, `SM_REGION`, `ROUTE53_REGION`). Read at cluster level by [External Secrets Operator](../k8s/argo_deployments/aws/secrets/) (which materializes `sm-creds`, `route53-creds`, `s3-creds`, `mlflow-config`, GHCR pull, repo clone creds, etc.) and at application level by [`cortexgrid.secrets`](../cortexgrid/secrets.py).

## Website infrastructure

### `terraform/website/` — Marketing website + investor auth

Serves the marketing site at **robodatalab.com** and the investor authentication backend.

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

### `terraform/platform/` — composition root

[main.tf](../terraform/platform/main.tf) holds the single backend (`s3://robolab-terraform-state/platform/terraform.tfstate`) and provider; [modules.tf](../terraform/platform/modules.tf) wires the four child modules with explicit input/output dependencies. One `terraform apply`, one state file. Child modules below describe what each one provisions.

#### `network/` — VPC + private subnets

| Resource | Purpose |
|----------|---------|
| VPC `10.0.0.0/16` | Project VPC with DNS hostnames + DNS support enabled |
| 1 public subnet | Hosts the NAT gateway only |
| 2 private subnets | EC2 head lands in `private[0]`; RDS subnet group needs both AZs |
| IGW + NAT gateway | Outbound internet for the private subnets (Tailscale auth, GHCR pulls) |
| S3 Gateway VPC endpoint | Free S3 access from private subnets — bypasses NAT, no egress charge |

#### `head/` — k3s head EC2

Provisions the EC2 instance that runs the k3s control plane, Argo CD, and platform services in `eu-west-2`. The DGX Spark joins as a worker over Tailscale.

| Resource | Purpose |
|----------|---------|
| EC2 (`t3.large`, Ubuntu 24.04 amd64, in private subnet, no public IP) | Hosts the k3s server + workloads pinned to `role=head` |
| EBS gp3 (100 GB default) | Mounted at `/storage`; backs k3s local-path PVCs. Online-resizable via `aws ec2 modify-volume`. |
| Security group | Egress-all only — no inbound. SSH and k3s API access are over Tailscale, which uses outbound DERP relays for inbound peer connections. |
| Cloud-init | Adds your `~/.ssh/id_rsa.pub` to the `ubuntu` user, installs Tailscale (joins tailnet via auth key from `.env`), formats and mounts the EBS volume. |

#### `s3/` — Data + mlflow-artifacts bucket

| Resource | Purpose |
|----------|---------|
| S3 bucket `robolab-data` | AES256, public access blocked. Used for cortexgrid data uploads and mlflow artifacts under `mlflow-artifacts/`. |
| IAM user policy | Attaches read/write to the existing `robolab-dgx` IAM user (also reused by ESO/in-cluster boto3). |
| SM `robolab/infra/S3_BUCKET_NAME` | Bucket name surfaced for ESO → `s3-creds` Secret → all consumer pods. |

#### `rds/` — Postgres for mlflow backend store

| Resource | Purpose |
|----------|---------|
| `db.t4g.micro` Postgres 16 | Single-AZ, encrypted gp3, ingress only from the head's SG. |
| Random master password | 32 chars, never leaves SM. |
| SM `robolab/infra/MLFLOW_BACKEND_STORE_URI` | Pre-composed `postgresql://...` URI, consumed directly by mlflow's `mlflow-config` ExternalSecret (no ESO templating). On-prem writes the same key with an in-cluster Postgres URI, so the workload manifest is profile-agnostic. |

### Service discovery

In-cluster service-to-service calls use cluster DNS (`mlflow.mlflow.svc.cluster.local:5000`,
`ray-head.ray.svc.cluster.local:8265`) — hardcoded in deployment manifests.

Service-discovery values (mlflow + ray URIs) flow through AWS Secrets Manager;
credentials and object-storage config flow through per-identity K8s Secrets
materialized by ESO. The cluster splits AWS-API access into three named
identities, each with its own SM key prefix and pod env-var prefix:

| Identity | Prefix | K8s Secret | Purpose |
|---|---|---|---|
| `SM_*` | `SM_` | `sm-creds` | AWS Secrets Manager (real AWS on both profiles) |
| `ROUTE53_*` | `ROUTE53_` | `route53-creds` | cert-manager DNS-01 (real AWS on both profiles) |
| `S3_*` | `S3_` | `s3-creds` | Object storage (real AWS on AWS profile, MinIO on on-prem) |

- `head-setup` writes the head's Tailscale IP and computed service URLs (`MLFLOW_TRACKING_URI`, `RAY_JOB_SERVER_URI`) to SM.
- AWS profile: terraform writes `MLFLOW_BACKEND_STORE_URI`, `NOTES_DB_URI` ([rds](../terraform/platform/rds/)), `S3_BUCKET_NAME`, `S3_ENDPOINT_URL`, `S3_REGION` ([s3](../terraform/platform/s3/)), `SM_REGION`, `ROUTE53_REGION` ([secrets](../terraform/platform/secrets/)). `PlatformConfig` mirrors `SM_*` into `ROUTE53_*` and `S3_*`.
- On-prem profile: two seed operators publish profile-specific service-discovery into SM (mirrors how `terraform/platform/{rds,s3}` composes these on AWS — both are *infrastructure-layer* publishers): [PostgresCredentials](../k8s/seed/operators/postgres_credentials.py) generates the postgres master password and writes `MLFLOW_BACKEND_STORE_URI` + `NOTES_DB_URI`; [MinioCredentials](../k8s/seed/operators/minio_credentials.py) generates the MinIO admin password and writes `S3_ENDPOINT_URL` (head tailscale IP + NodePort), `S3_REGION`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. `PlatformConfig` mirrors `SM_*` only into `ROUTE53_*` (object storage is MinIO and stays untouched).
- ESO syncs `robolab/infra/*` into k8s Secrets (`sm-creds`, `route53-creds`, `s3-creds`, `mlflow-config`); Reflector mirrors them into every workload namespace.

#### Profile-aware values in SM

Workload manifests reference Secret *names*, not specific backends. The Secret *contents* differ by profile:

| SM key | AWS source | on-prem source |
|---|---|---|
| `SM_ACCESS_KEY_ID` / `SM_SECRET_ACCESS_KEY` | `EnvSecrets` from `.env` (real AWS keys for SM access) | same |
| `SM_REGION` / `ROUTE53_REGION` | `terraform/platform/secrets` (`eu-west-2`) | not yet driven from terraform; supply via `.env` |
| `ROUTE53_ACCESS_KEY_ID` / `ROUTE53_SECRET_ACCESS_KEY` | `PlatformConfig` mirrors `SM_*` | same |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | [`PlatformConfig`](../k8s/seed/operators/platform_config.py) mirrors `SM_*` | [`MinioCredentials`](../k8s/seed/operators/minio_credentials.py) writes MinIO admin creds (user `admin`, random password persisted in the cluster `minio-credentials` Secret) |
| `S3_REGION` | `terraform/platform/s3` (`eu-west-2`) | [`MinioCredentials`](../k8s/seed/operators/minio_credentials.py) writes `us-east-1` (MinIO ignores, boto3 needs a value) |
| `S3_BUCKET_NAME` | `terraform/platform/s3` (`robolab-data`) | [`MinioCredentials`](../k8s/seed/operators/minio_credentials.py) writes `mlflow-artifacts` |
| `S3_ENDPOINT_URL` | `terraform/platform/s3` (regional AWS S3 URL) | [`MinioCredentials`](../k8s/seed/operators/minio_credentials.py) writes in-cluster MinIO Service URL |
| `MLFLOW_BACKEND_STORE_URI` | `terraform/platform/rds` (composed from RDS attrs) | [`PostgresCredentials`](../k8s/seed/operators/postgres_credentials.py) composes from generated password + head tailscale IP |
| `NOTES_DB_URI` | `terraform/platform/rds` (composed from RDS attrs) | [`PostgresCredentials`](../k8s/seed/operators/postgres_credentials.py) composes from generated password + head tailscale IP |

Three cluster Secrets, three purposes:

- `sm-creds` carries `SM_ACCESS_KEY_ID/SECRET/REGION`. Consumed via `envFrom` by every pod that uses [`cortexgrid.secrets`](../cortexgrid/secrets.py), which reads them explicitly and builds an SM-only boto3 client. Used by jobs-control-plane, cortexgrid-ui-backend, ray-head, ray-worker, integration-test Jobs.
- `route53-creds` carries `ROUTE53_ACCESS_KEY_ID/SECRET/REGION`. Read by the cert-manager `ClusterIssuer` for DNS-01 ACME challenges.
- `s3-creds` carries `S3_ACCESS_KEY_ID/SECRET/REGION/ENDPOINT_URL/BUCKET_NAME`. Consumed by [`cortexgrid.s3_util`](../cortexgrid/s3_util.py) (which builds its boto3 client explicitly from the `S3_*` env), and by mlflow + loki (third-party servers that require the `AWS_*` env var shape — their pod specs map `S3_*` → `AWS_*` explicitly).

The three env-var namespaces never collide; nothing flows through boto3's default `AWS_*` chain in our code paths.

### `terraform/platform/secrets/` — Centralized secrets

IAM users, roles, and OIDC configuration for accessing AWS Secrets Manager. Secret values are written by `setup-node` from `.env` (see [k8s/seed/operators/env_secrets.py](../k8s/seed/operators/env_secrets.py)), not Terraform.

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
| `model-gateway` | LLM provider abstraction layer |
| `model-training` | Training facilities (SFT, LoRA) |

## Prerequisites

- Terraform >= 1.5
- AWS CLI configured with appropriate credentials
- [uv](https://docs.astral.sh/uv/) for Python dependency management
- Tailscale on Mac and DGX (for ML compute)

## Further documentation

- [k8s/README.md](k8s/README.md) — GitOps overview + bootstrap FAQ
- [k8s/argo_deployments/](../k8s/argo_deployments/) — one-pager README per platform component (Ray, MLflow, monitoring, secrets, NVIDIA device plugin, jobs control plane; on-prem-only: MinIO, Postgres)
- [cortexgrid/README.md](cortexgrid/README.md) — Python library reference
- [tests/INTEGRATION.md](tests/INTEGRATION.md) — integration test platform: trigger flow, secrets, how to add a target
