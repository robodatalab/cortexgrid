# RoboLab Infrastructure

Cloud infrastructure, ML compute, and deployment orchestration for RoboLab. All cloud resources run on AWS (`us-east-1`) and are provisioned with Terraform. ML compute runs on a DGX Spark accessible over Tailscale.

## Architecture

```
  AWS (us-east-1)
  +--------------------------------------------------+
  |                                                  |
  |  Route53 --> CloudFront --> S3 (robodatalab.com) |
  |                  |                               |
  |            API Gateway --> Lambda (auth)          |
  |                              |                   |
  |                     VPC [ RDS + NAT ]            |
  |                                                  |
  |  Secrets Manager (robolab/auth/*, robolab/infra/*)|
  |       ^                                          |
  |       |  OIDC                                    |
  |  GitHub Actions                                  |
  +--------------------------------------------------+

  Tailscale Network
  +-------------+         +---------------------------+
  |  MacBook    |  SSH    |  DGX Spark (128GB VRAM)   |
  |             |-------->|                           |
  |  your code  |         |  Ray         :8265        |
  |  (cortexflow)         |  MLflow      :5000        |
  |             |         |  MinIO       :9000        |
  |             |         |  Grafana     :3000        |
  |             |         |  Prometheus  :9090        |
  |             |         |  Redis, PostgreSQL        |
  +-------------+         +---------------------------+
```

### Folder layout

| Folder | Purpose |
|--------|---------|
| `terraform/website/` | Marketing site + investor auth infrastructure |
| `terraform/platform/secrets/` | Centralized secrets management (AWS Secrets Manager + IAM) |
| `k8s/` | Argo CD GitOps platform — bootstrap manifest, Argo Applications, workload manifests, one-shot DGX seed scripts |
| `cortexflow/` | Python library for ML code to reach Ray/MLflow/S3 |
| `lambda/auth/` | Auth Lambda source (deployed by `terraform/website/`) |

## cortexflow

`cortexflow` is a Python library that connects your ML code to the deployed infrastructure. It wraps Ray, MLflow, and S3/MinIO so your training scripts don't need to know about URLs, credentials, or service endpoints.

See [cortexflow/README.md](cortexflow/README.md) for full reference

### Setup

**Prerequisites:** Mac and DGX on the same Tailscale network. DGX has `nvidia-container-toolkit` installed. Mac has `uv`, `kubectl`, `sshpass`, and AWS credentials with access to `robolab/*` secrets.

One-shot seed (run once per DGX lifetime):

```bash
bash k8s/seed/setup-dgx.sh
```

That script installs k3s on the DGX, drops [k8s/argocd.yaml](k8s/argocd.yaml) onto the cluster, publishes [k8s/seed/.env](k8s/seed/.env) entries to AWS Secrets Manager under `robolab/argocd/*`, and merges the DGX kubeconfig into `~/.kube/config` as context `dgx`. Argo CD then reconciles everything under [k8s/argo-deployments/](k8s/argo-deployments/) from `main`. After seeding, all changes flow through git pushes.

Teardown (wipes the cluster):

```bash
bash k8s/seed/teardown-dgx.sh
```

### Secrets management

Two independent paths, both backed by AWS Secrets Manager:

- **Cluster bootstrap** — `robolab/argocd/*` → [External Secrets Operator](k8s/argo-deployments/secrets/) materializes k8s Secrets (GHCR pull, repo clone creds, etc.).
- **Application code** — `robolab/infra/*` → `cortexflow.secrets` reads directly from AWS SM at runtime.

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

### `terraform/platform/secrets/` — Centralized secrets

IAM users, roles, and OIDC configuration for accessing AWS Secrets Manager. Secret values are managed directly in AWS Secrets Manager (via the Platform UI or `aws secretsmanager` CLI), not Terraform.

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
- [k8s/argo-deployments/](k8s/argo-deployments/) — one-pager README per platform component (Ray, MLflow, MinIO, Postgres, monitoring, secrets, NVIDIA device plugin, jobs control plane)
- [cortexflow/README.md](cortexflow/README.md) — Python library reference
