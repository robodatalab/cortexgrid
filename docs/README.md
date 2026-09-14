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
  |  Secrets Manager (robolab/auth/*)                |
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
  |             |    |    jobs-control-plane,    |    |                           |
  |             |    |    secrets server (host)  |    |                           |
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
| `terraform/platform/secrets/` | `robolab-dgx` IAM user (Route53 + S3 access) and the GitHub Actions OIDC provider |
| `terraform/platform/network/` | VPC, public/private subnets, NAT, S3 Gateway endpoint |
| `terraform/platform/head/` | EC2 + EBS that hosts the k3s head and platform services |
| `terraform/platform/s3/` | Data + mlflow-artifacts bucket and IAM grant on `robolab-dgx` |
| `terraform/platform/rds/` | Postgres for mlflow backend store; password generated, URIs exposed as outputs |
| `k8s/` | Argo CD GitOps platform — bootstrap manifest, Argo Applications, workload manifests, one-shot seed scripts |
| `cortexgrid/` | Python library for ML code to reach Ray/MLflow/S3 |
| `lambda/auth/` | Auth Lambda source (deployed by `terraform/website/`) |

## cortexgrid

`cortexgrid` is a Python library that connects your ML code to the deployed infrastructure. It wraps Ray, MLflow, and S3/MinIO so your training scripts don't need to know about URLs, credentials, or service endpoints.

See [cortexgrid/README.md](cortexgrid/README.md) for full reference

### Setup

**Prerequisites:** Mac on the Tailscale network, with `uv`, `kubectl` and `terraform`. A `.env.head` file at the repo root, copied from [.env.head.template](../.env.head.template) and filled in with the values head setup can't generate: GitHub token and App, Tailscale operator client, Route53 keys (on-prem) and `TAILSCALE_AUTH_KEY` (AWS). On the AWS profile, also AWS credentials that can apply and read the `terraform/platform` and `terraform/platform/secrets` stacks.

**1. Provision the AWS head infrastructure** (terraform — VPC, EC2, S3, RDS, all in one shot):

```bash
make head-aws-apply
```

This runs a single `terraform apply` against the root composition at [terraform/platform/](../terraform/platform/), which provisions network → head → s3 → rds in dependency order, then writes `Host robolab-aws <ip>` into `~/.ssh/config`. The EC2 boots, joins your tailnet as `robolab-head`, and mounts the EBS volume at `/storage`.

**2. Seed the head** (k3s + ArgoCD bootstrap):

```bash
make head-setup IP=<robolab-head-tailscale-ip> STORAGE_PATH=/storage PROFILE=aws
```

**3. Seed a worker** (DGX joins as GPU worker):

```bash
make worker-setup IP=<dgx-tailscale-ip> PROFILE=aws
```

**4. Point your laptop at the head** (for `cortexgrid` and `make dev`):

```bash
export CORTEXGRID_HEAD_URL=http://robolab-head:7700
```

**Teardown:**

```bash
make node-teardown IP=<tailscale-ip>      # k3s teardown on a single node
make head-aws-destroy                     # single terraform destroy of the platform stack
```

`head-setup` starts the head secrets server and publishes `.env.head` (plus terraform outputs on AWS) to it, installs k3s, stages [k8s/argocd.yaml](../k8s/argocd.yaml), publishes the k3s token + service URLs to the secrets server, merges the kubeconfig into `~/.kube/config` as context `robolab`, and labels the node `role=head`. Argo CD then reconciles everything under [k8s/argo_deployments/](../k8s/argo_deployments/) from `main`. Topology is recorded in [infra-config.yaml](../infra-config.yaml) at the repo root.

### Secrets management

Every secret lives on the head in `/etc/cortexgrid/.env`, served by a small HTTP server ([cortexgrid_head.py](../k8s/seed/scripts/cortexgrid_head.py), systemd unit `cortexgrid-head`, port 7700). It runs on the host rather than in k8s, so it is up before the cluster, and teardown removes the service but keeps the file. There is no authentication: the server is only reachable over the tailnet.

- **API:** `GET /secrets` lists ids; `GET`, `PUT` and `DELETE /secrets/<id>` read, write and remove one value as `{"value": "..."}`.
- **Writers:** head setup (`EnvSecrets` publishes every `.env.head` key; `TerraformOutputs`, `PostgresCredentials`, `MinioCredentials` and `ControlPlaneDetails` publish what they generate or read, see [Service discovery](#service-discovery)) and anyone calling `cortexgrid.set_secret`, such as the UI's Secrets page.
- **Readers:** [`cortexgrid.secrets`](../cortexgrid/secrets.py) at `$CORTEXGRID_HEAD_URL` (`http://robolab-head:7700` from a laptop, `http://cortexgrid-head.default.svc.cluster.local:7700` from pods, via a selector-less Service that `BootstrapSecrets` points at the head), and the [External Secrets Operator](../k8s/argo_deployments/base/secrets/), whose `cortexgrid-head` ClusterSecretStore uses the webhook provider to materialize `route53-creds`, `s3-creds`, `mlflow-config`, `ray-env`, GHCR pull, repo clone creds, etc.

To carry values over from a cluster that still used AWS Secrets Manager, run `uv run python scripts/export_sm_to_env.py` and merge its output into `.env.head`.

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
| Cloud-init | Adds your `~/.ssh/id_rsa.pub` to the `ubuntu` user, installs Tailscale (joins tailnet via auth key from `.env.head`), formats and mounts the EBS volume. |

#### `s3/` — Data + mlflow-artifacts bucket

| Resource | Purpose |
|----------|---------|
| S3 bucket `robolab-data` | AES256, public access blocked. Used for cortexgrid data uploads and mlflow artifacts under `mlflow-artifacts/`. |
| IAM user policy | Attaches read/write to the existing `robolab-dgx` IAM user, whose keys head setup publishes as `S3_*`. |

#### `rds/` — Postgres for mlflow backend store

| Resource | Purpose |
|----------|---------|
| `db.t4g.micro` Postgres 16 | Single-AZ, encrypted gp3, ingress only from the head's SG. |
| Random master password | 32 chars, lives only in tfstate and the composed URIs. |
| Outputs `mlflow_backend_store_uri`, `notes_db_uri` | Pre-composed `postgresql://...` URIs. Head setup publishes them as `MLFLOW_BACKEND_STORE_URI` and `NOTES_DB_URI`; mlflow's `mlflow-config` ExternalSecret reads the first directly. On-prem writes the same keys with in-cluster Postgres URIs, so the workload manifest is profile-agnostic. |

### Service discovery

In-cluster service-to-service calls use cluster DNS (`mlflow.mlflow.svc.cluster.local:5000`,
`ray-head.ray.svc.cluster.local:8265`) — hardcoded in deployment manifests.

Service-discovery values (mlflow + ray URIs) and credentials all flow through
the head secrets store; pods receive credentials through per-identity K8s
Secrets materialized by ESO. Two AWS-API identities, each with its own env-var
prefix:

| Identity | K8s Secret | Purpose |
|---|---|---|
| `ROUTE53_*` | `route53-creds` | cert-manager DNS-01 (real AWS on both profiles) |
| `S3_*` | `s3-creds` | Object storage (real AWS on AWS profile, MinIO on on-prem) |

- `head-setup` writes the head's Tailscale IP and computed service URLs (`MLFLOW_TRACKING_URI`, `RAY_JOB_SERVER_URI`, `RAY_SERVE_URI`).
- AWS profile: [TerraformOutputs](../k8s/seed/operators/terraform_outputs.py) publishes `MLFLOW_BACKEND_STORE_URI`, `NOTES_DB_URI`, `S3_BUCKET_NAME`, `S3_ENDPOINT_URL` and `S3_REGION` from [terraform/platform](../terraform/platform/outputs.tf) outputs, and the `robolab-dgx` user's keys from [secrets](../terraform/platform/secrets/) outputs as both `S3_*` and `ROUTE53_*`.
- On-prem profile: two seed operators publish profile-specific service-discovery (the on-prem analog of `TerraformOutputs` — both are *infrastructure-layer* publishers): [PostgresCredentials](../k8s/seed/operators/postgres_credentials.py) generates the postgres master password and writes `MLFLOW_BACKEND_STORE_URI` + `NOTES_DB_URI`; [MinioCredentials](../k8s/seed/operators/minio_credentials.py) generates the MinIO admin password and writes `S3_ENDPOINT_URL` (head tailscale IP + NodePort), `S3_REGION`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. `ROUTE53_*` come from `.env.head`.
- ESO syncs the store into k8s Secrets (`route53-creds`, `s3-creds`, `mlflow-config`); Reflector mirrors them into every workload namespace.

#### Profile-aware values

Workload manifests reference Secret *names*, not specific backends. The Secret *contents* differ by profile:

| Key | AWS source | on-prem source |
|---|---|---|
| `ROUTE53_ACCESS_KEY_ID` / `ROUTE53_SECRET_ACCESS_KEY` | [`TerraformOutputs`](../k8s/seed/operators/terraform_outputs.py) (`robolab-dgx` user) | `.env.head` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | [`TerraformOutputs`](../k8s/seed/operators/terraform_outputs.py) (`robolab-dgx` user) | [`MinioCredentials`](../k8s/seed/operators/minio_credentials.py) writes MinIO admin creds (user `admin`, random password persisted in the cluster `minio-credentials` Secret) |
| `S3_REGION` | `TerraformOutputs` (`eu-west-2`) | [`MinioCredentials`](../k8s/seed/operators/minio_credentials.py) writes `us-east-1` (MinIO ignores, boto3 needs a value) |
| `S3_BUCKET_NAME` | `TerraformOutputs` (`robolab-data`) | [`MinioCredentials`](../k8s/seed/operators/minio_credentials.py) writes `mlflow-artifacts` |
| `S3_ENDPOINT_URL` | `TerraformOutputs` (regional AWS S3 URL) | [`MinioCredentials`](../k8s/seed/operators/minio_credentials.py) writes in-cluster MinIO Service URL |
| `MLFLOW_BACKEND_STORE_URI` | `TerraformOutputs` (composed from RDS attrs) | [`PostgresCredentials`](../k8s/seed/operators/postgres_credentials.py) composes from generated password + head tailscale IP |
| `NOTES_DB_URI` | `TerraformOutputs` (composed from RDS attrs) | [`PostgresCredentials`](../k8s/seed/operators/postgres_credentials.py) composes from generated password + head tailscale IP |

Two cluster Secrets, two purposes:

- `route53-creds` carries `ROUTE53_ACCESS_KEY_ID/SECRET`. Read by the cert-manager `ClusterIssuer` for DNS-01 ACME challenges (the issuer pins the region).
- `s3-creds` carries `S3_ACCESS_KEY_ID/SECRET/REGION/ENDPOINT_URL/BUCKET_NAME`. Consumed by [`cortexgrid.s3_util`](../cortexgrid/s3_util.py) and by mlflow + loki (third-party servers that require the `AWS_*` env var shape — their pod specs map `S3_*` → `AWS_*` explicitly).

Pods that call [`cortexgrid.secrets`](../cortexgrid/secrets.py) (jobs-control-plane, cortexgrid-ui-backend, ray-head, ray-worker, integration-test Jobs) get `CORTEXGRID_HEAD_URL` instead of a credentials Secret.

The env-var namespaces never collide; nothing flows through boto3's default `AWS_*` chain in our code paths.

### `terraform/platform/secrets/` — `robolab-dgx` IAM user

The `robolab-dgx` IAM user: its Route53 policy lives here, its S3 policy in [terraform/platform/s3](../terraform/platform/s3/). On the AWS profile head setup publishes the user's keys from this stack's outputs. The stack also keeps the account's GitHub Actions OIDC provider.

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

The secrets module keeps an `aws_iam_openid_connect_provider` trusting `token.actions.githubusercontent.com`. No role in this repo uses it; the one that did only read AWS Secrets Manager and is gone.

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
