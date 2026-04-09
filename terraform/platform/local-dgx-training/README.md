# RoboLab ML Training Infrastructure

GPU-accelerated ML compute running on a DGX Spark, orchestrated from a MacBook over Tailscale. Designed for seamless expansion to AWS EC2 nodes with zero code changes.

## What's here

```
cortexflow/          Python library — import cortexflow in your projects
docker-compose.yml   Services deployed on the DGX Spark
scripts/             Setup, teardown, secrets management
monitoring/          Prometheus + Grafana config
storage/             MinIO bucket initialization
docs/                Architecture, secrets, troubleshooting
tests/               cortexflow unit tests
```

## Setup

**Prerequisites:** Mac and DGX on the same Tailscale network. DGX has Docker + NVIDIA Container Toolkit.

```bash
cd terraform/platform/local-dgx-training

# 1. Configure secrets and shell env vars
make setup-mac

# 2. Deploy stack to DGX (SSH)
make setup-dgx

# 3. Verify
make health
```

## Using cortexflow

Add to your project's `pyproject.toml`:

```toml
[project]
dependencies = ["cortexflow"]

[tool.uv.sources]
cortexflow = { git = "https://github.com/paksas/robolab-infra.git", subdirectory = "terraform/platform/local-dgx-training" }

[tool.hatch.metadata]
allow-direct-references = true
```

Then in your code:

```python
import cortexflow

cortexflow.init()

# MLflow experiment tracking
with cortexflow.mlflow_run("my-experiment") as run:
    cortexflow.log_metric("loss", 0.5, step=1)
    cortexflow.save_checkpoint(model, optimizer, epoch=5)

# Distributed compute on the DGX
@cortexflow.remote(num_gpus=1, max_retries=3)
def train(config):
    ...

cortexflow.get(train.remote({"lr": 1e-3}))
```

Run as normal: `uv run python train.py`

See the [root README](../../../README.md#cortexflow) for the full API reference.

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Ray | 8265 | Job scheduling, distributed compute |
| MLflow | 5000 | Experiment tracking, model registry |
| MinIO | 9000/9001 | S3-compatible artifact storage |
| PostgreSQL | 5432 | MLflow metadata backend |
| Redis | 6379 | Ray GCS persistence |
| Prometheus | 9090 | Metrics collection |
| Grafana | 3000 | Dashboards |

## Make targets

| Target | Description |
|--------|-------------|
| `setup-mac` | Configure secrets and shell environment |
| `setup-dgx` | Deploy stack to DGX via SSH |
| `teardown-dgx` | Stop stack, remove volumes and .env on DGX |
| `teardown-mac` | Remove shell exports and local .env |
| `push-secrets` | Push .env secrets to AWS Secrets Manager |
| `pull-secrets` | Pull secrets from AWS Secrets Manager |
| `health` | Check all services are reachable |
| `up` | Start all services |
| `down` | Stop all services |
| `logs` | Tail service logs |

## Documentation

- [Architecture](docs/architecture.md)
- [Secrets Management](docs/secrets.md)
- [Adding AWS Nodes](docs/adding-aws-nodes.md)
- [Troubleshooting](docs/troubleshooting.md)
