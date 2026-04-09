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

### Installation

Add `cortexflow` as a dependency in your project's `pyproject.toml`:

```toml
[project]
dependencies = [
    "cortexflow",
]

[tool.uv.sources]
cortexflow = { git = "https://github.com/paksas/robolab-infra.git", subdirectory = "terraform/platform/local-dgx-training" }

[tool.hatch.metadata]
allow-direct-references = true
```

Then `uv sync` to install it.

### Usage

```python
import cortexflow

cortexflow.init()
```

That single call reads `RAY_ADDRESS`, `MLFLOW_TRACKING_URI`, and `DGX_TAILSCALE_IP` from your shell environment (set by `make setup-mac`) and connects to all services.

#### Experiment tracking (MLflow)

```python
with cortexflow.mlflow_run("my-experiment", run_name="v3") as run:
    cortexflow.log_params({"lr": 1e-3, "epochs": 20, "batch_size": 64})

    for epoch in range(20):
        loss = train_one_epoch(model, dataloader)
        cortexflow.log_metric("loss", loss, step=epoch)

        if epoch % 5 == 0:
            cortexflow.save_checkpoint(model, optimizer, epoch=epoch)
```

Metrics and artifacts are logged to the MLflow server on the DGX. View them at `http://<DGX_IP>:5000`.

#### Resuming from a checkpoint

```python
checkpoint = cortexflow.load_checkpoint(run_id="abc123")
model.load_state_dict(checkpoint["model_state_dict"])
optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
start_epoch = checkpoint["epoch"] + 1
```

#### Distributed compute (Ray)

```python
@cortexflow.remote(num_gpus=1, max_retries=3)
def train_step(batch):
    # runs on the DGX GPU
    # MLflow and S3 env vars are injected automatically
    return loss

futures = [train_step.remote(b) for b in batches]
results = cortexflow.get(futures)
```

`cortexflow.remote` wraps `@ray.remote` and automatically:
- Reads your project's `pyproject.toml` to build the pip dependency list (including `[tool.uv.sources]` git refs)
- Sets `working_dir` to your project root
- Excludes `.venv/`, `.git/`, `__pycache__/`, etc.
- Injects MLflow/S3 credentials so task code running on the DGX can reach all services

#### Object storage (S3/MinIO)

```python
cortexflow.upload("data/output.parquet", bucket="ray-checkpoints", key="run-42/output.parquet")
cortexflow.download("ray-checkpoints", "run-42/output.parquet", local_path="./output.parquet")

# or get the raw boto3 client
s3 = cortexflow.get_s3_client()
```

Works with MinIO on the DGX today, real S3 on AWS tomorrow — same code.

#### Getting raw clients

```python
mlflow_client = cortexflow.get_mlflow_client()   # mlflow.tracking.MlflowClient
ray_client = cortexflow.get_ray_client()         # ray.job_submission.JobSubmissionClient
s3_client = cortexflow.get_s3_client()            # boto3 S3 client
```

### API reference

| Function | Description |
|----------|-------------|
| `cortexflow.init()` | Configure all connections from env vars. Call once. |
| `cortexflow.mlflow_run(experiment, ...)` | Context manager for an MLflow run |
| `cortexflow.log_metric(key, value, step)` | Log a metric |
| `cortexflow.log_metrics(metrics, step)` | Log multiple metrics |
| `cortexflow.log_params(params)` | Log parameters |
| `cortexflow.log_artifact(path, artifact_path)` | Log a file as an artifact |
| `cortexflow.save_checkpoint(model, optimizer, epoch)` | Save a PyTorch checkpoint to MLflow |
| `cortexflow.load_checkpoint(run_id, epoch)` | Load a checkpoint from MLflow |
| `cortexflow.remote(**kwargs)` | Decorator wrapping `@ray.remote` — auto-builds runtime_env from pyproject.toml |
| `cortexflow.get(futures)` | `ray.get()` alias |
| `cortexflow.upload(path, bucket, key)` | Upload a file to S3/MinIO |
| `cortexflow.download(bucket, key, path)` | Download a file from S3/MinIO |
| `cortexflow.get_mlflow_client()` | Raw configured MLflow client |
| `cortexflow.get_ray_client()` | Raw configured Ray JobSubmissionClient |
| `cortexflow.get_s3_client()` | Raw configured boto3 S3 client |

## ML compute stack

The DGX Spark runs the following services via Docker Compose:

| Service | Port | Purpose |
|---------|------|---------|
| Ray | 8265 | Job scheduling, distributed compute |
| MLflow | 5000 | Experiment tracking, model registry |
| MinIO | 9000/9001 | S3-compatible artifact storage |
| PostgreSQL | 5432 | MLflow metadata backend |
| Redis | 6379 | Ray GCS persistence (fault tolerance) |
| Prometheus | 9090 | Metrics collection |
| Grafana | 3000 | Dashboards (GPU, jobs, system) |

### Setup

**Prerequisites:** Both Mac and DGX on the same Tailscale network. DGX has Docker + NVIDIA Container Toolkit.

```bash
cd terraform/platform/local-dgx-training

# 1. Configure Mac (secrets, shell env vars)
make setup-mac

# 2. Deploy stack to DGX (syncs files, starts containers)
make setup-dgx

# 3. Verify
make health
```

### Make targets

| Target | Description |
|--------|-------------|
| `make setup-mac` | Configure secrets and shell environment |
| `make setup-dgx` | Deploy stack to DGX via SSH |
| `make teardown-dgx` | Stop stack, delete volumes and .env on DGX |
| `make teardown-mac` | Remove shell exports and local .env |
| `make push-secrets` | Push .env secrets to AWS Secrets Manager |
| `make pull-secrets` | Pull secrets from AWS Secrets Manager |
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

IAM users, roles, and OIDC configuration for accessing AWS Secrets Manager. Secret values are managed by `scripts/push-secrets.sh`, not Terraform.

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
