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
| `terraform/platform/local-dgx-training/` | ML compute stack (Docker Compose) + `cortexflow` library |
| `lambda/auth/` | Auth Lambda source (deployed by `terraform/website/`) |

## cortexflow

`cortexflow` is a Python library that connects your ML code to the deployed infrastructure. It wraps Ray, MLflow, and S3/MinIO so your training scripts don't need to know about URLs, credentials, or service endpoints.

See [cortexflow/README.md](cortexflow/README.md) for full reference

### Setup

**Prerequisites:** Both Mac and DGX on the same Tailscale network. DGX has Docker + NVIDIA Container Toolkit.

```bash
cd terraform/platform/local-dgx-training

# 1. Verify Mac prerequisites (aws CLI + Secrets Manager access)
make setup-mac

# 2. Deploy stack to DGX (fetches secrets from AWS SM, syncs files, starts containers)
make setup-dgx

# 3. Verify
make health
```

### Make targets

| Target | Description |
|--------|-------------|
| `make setup-mac` | Verify Mac prerequisites (aws CLI + SM access) |
| `make setup-dgx` | Deploy stack to DGX via SSH |
| `make teardown-dgx` | Stop stack, delete volumes and .env on DGX |
| `make health` | Check all services are reachable |
| `make up` | Start all services |
| `make down` | Stop all services |
| `make logs` | Tail service logs |

### Secrets management

Secrets are stored in AWS Secrets Manager under the `robolab/infra/*` namespace. See [docs/secrets.md](terraform/platform/local-dgx-training/docs/secrets.md) for the full workflow.

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

- [Architecture](terraform/platform/local-dgx-training/docs/architecture.md) — design decisions and data flow
- [Secrets Management](terraform/platform/local-dgx-training/docs/secrets.md) — AWS Secrets Manager workflow
- [Adding AWS Nodes](terraform/platform/local-dgx-training/docs/adding-aws-nodes.md) — scaling to AWS EC2
- [Troubleshooting](terraform/platform/local-dgx-training/docs/troubleshooting.md) — common issues and fixes
