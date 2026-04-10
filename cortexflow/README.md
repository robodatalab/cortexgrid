# cortexflow

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