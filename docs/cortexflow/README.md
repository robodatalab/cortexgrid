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
cortexflow = { git = "https://github.com/robodatalab/robolab-infra.git", subdirectory = "cortexflow" }

[tool.hatch.metadata]
allow-direct-references = true
```

Then `uv sync` to install it.

### Usage

```python
import cortexflow

cortexflow.init(experiment="weather-forecast")
```

That single call fetches `CONTROL_PLANE_TAILSCALE_IP` from AWS Secrets Manager (using your local AWS credentials) and connects to all services via its Tailscale-derived URLs. It also creates (or finds) the named MLflow experiment and starts a new run inside it. Omit `experiment=` to auto-generate a unique name like `funky-koval-12`.

**One experiment per binary run.** `cortexflow.init()` may only be called once per process. Every subsequent `cortexflow.log_metric`, `cortexflow.log_artifact`, checkpoint, and `cortexflow.remote()` submission is scoped to that experiment+run. Remote jobs dispatched by the control plane inherit the experiment+run via the pickled payload, so their logging flows into the same MLflow run as the parent binary.

#### Experiment tracking (MLflow)

```python
cortexflow.init(experiment="weather-forecast")

cortexflow.log_params({"lr": 1e-3, "epochs": 20, "batch_size": 64})

for epoch in range(20):
    loss = train_one_epoch(model, dataloader)
    cortexflow.log_metric("loss", loss, step=epoch)

    if epoch % 5 == 0:
        with cortexflow.checkpoint() as ckpt:
            ckpt.epoch = epoch
            ckpt.save_training_state(model, optimizer)
```

No run-scoping context manager — `init()` starts the run, and every subsequent logging call flows into it. Metrics and artifacts are logged to the MLflow server on the DGX. View them at `http://<DGX_IP>:5000`.

#### Checkpointing and resuming

Inside a cortexflow job, `cortexflow.checkpoint()` returns an attribute-based checkpoint object that persists to MLflow artifacts when its `with` block exits. On job restart (either manual retry or `retry=True`), `cortexflow.resume()` returns the last checkpoint for the same job ID, or `None` if there isn't one.

```python
ckpt = cortexflow.resume()
if ckpt:
    ckpt.restore_training_state(model, optimizer)
    start_epoch = ckpt.epoch + 1
else:
    start_epoch = 0

for epoch in range(start_epoch, 20):
    train_one_epoch(model, dataloader)
    with cortexflow.checkpoint() as ckpt:
        ckpt.epoch = epoch
        ckpt.save_training_state(model, optimizer)
```

You can assign any cloudpickle-compatible or torch-serializable value as an attribute on the checkpoint (`ckpt.metric = 0.93`, `ckpt.weights = model.state_dict()`); the `save_training_state`/`restore_training_state` helpers are a shortcut for the common model+optimizer pair.

#### Distributed compute (jobs control plane)

```python
def train_step(batch):
    # runs on the DGX GPU
    # MLflow and S3 env vars are injected automatically
    return loss

job_id = cortexflow.remote(train_step, batch, num_gpus=1, retry=True)
print(f"Submitted: {job_id}")
```

`cortexflow.remote` submits a job *request* (a pickled payload plus a `JobLifecycle` record) to MLflow and returns a job ID string immediately. It does not wait for the job to run or finish — use the UI at `http://<DGX_IP>:8000`, or poll `cortexflow.list_experiment_run_jobs(run_id)`, to observe status.

A separate service — the **jobs control plane** — polls MLflow for pending job requests, matches them against the set of Ray submissions the cluster already has, and submits anything missing. It is also responsible for retrying failed jobs and honouring user-requested stops.

Each submission captures your project's code and dependencies automatically:
- Reads your project's `pyproject.toml` to build the pip dependency list (including `[tool.uv.sources]` git refs)
- Sets `working_dir` to your project root
- Excludes `.venv/`, `.git/`, `__pycache__/`, etc.
- Injects MLflow/S3 credentials so task code running on the DGX can reach all services

##### Retries

Pass `retry=True` and the control plane will resubmit the job whenever Ray reports the most recent attempt as `FAILED`. Retries are **unbounded by design**: the intended way to end a retry loop is to stop the job manually from the UI (which flips the `stop_requested` latch on the lifecycle, and the control plane stops the current Ray attempt on its next poll). This keeps the retry policy simple — you don't have to predict a good `max_retries` up front — and puts the human in the loop for anything that's failing persistently.

##### Stopping a job

```python
cortexflow.stop_experiment_run_jobs(run_id)   # stops every job in the run
```

`stop_experiment_run_jobs` never touches Ray directly. It only flips `stop_requested` on each job's lifecycle record in MLflow. The control plane observes the flag on its next poll and calls `ray.stop_job` for any attempt that has reached Ray. For jobs that have not yet been submitted, the same flag short-circuits the submission path inside the worker.

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
s3_client = cortexflow.get_s3_client()           # boto3 S3 client
```

#### Model registry and serving

Save a trained model's weights together with the serve-app that fronts it, then deploy it as a Ray Serve application:

```python
saved = cortexflow.save_model(weights_dir, MyServeApp, family="qwen", suffix="instruct")
deployed = cortexflow.deploy_model("qwen", "instruct", saved.run_name, wait=True)
print(deployed.url)
```

`save_model` is synchronous (registry lifecycle: `uploading` -> `ready`); `deploy_model` schedules the serving lifecycle (`deploying` -> `running`). See [model-serving.md](model-serving.md) for both lifecycles end to end - upload/deploy/undeploy/delete, status queries (`model_registry_status`, `model_serving_status`), and error handling.

### API reference

| Function | Description |
|----------|-------------|
| `cortexflow.init(experiment=None)` | Configure connections + start a new MLflow run inside the named experiment. One call per binary. |
| `cortexflow.log_metric(key, value, step)` | Log a metric |
| `cortexflow.log_metrics(metrics, step)` | Log multiple metrics |
| `cortexflow.log_params(params)` | Log parameters |
| `cortexflow.log_artifact(path, artifact_path)` | Log a file as an artifact |
| `cortexflow.checkpoint()` | Context manager returning an attribute-based checkpoint saved to MLflow on exit |
| `cortexflow.resume()` | Load the latest checkpoint for the current job, or `None` |
| `cortexflow.remote(fn, *args, num_gpus=0, num_cpus=1, retry=False, **kwargs)` | Submit a function to the jobs control plane; returns a job ID |
| `cortexflow.list_experiment_run_jobs(run_id)` | List `JobLifecycle` records for every cortexflow job in a run |
| `cortexflow.stop_experiment_run_jobs(run_id)` | Request every job in a run to stop (flips the `stop_requested` latch) |
| `cortexflow.get_ray_job_status(ray_job_id)` | Live Ray status for a submission id |
| `cortexflow.get_ray_logs(ray_job_id)` | Tail the stdout/stderr of a Ray submission |
| `cortexflow.upload(path, bucket, key)` | Upload a file to S3/MinIO |
| `cortexflow.download(bucket, key, path)` | Download a file from S3/MinIO |
| `cortexflow.get_mlflow_client()` | Raw configured MLflow client |
| `cortexflow.get_s3_client()` | Raw configured boto3 S3 client |

## ML compute stack

The DGX Spark runs the following services as k8s workloads managed by Argo CD (see [../k8s/argo_deployments/](../../k8s/argo_deployments/)):

| Service | Port | Purpose |
|---------|------|---------|
| Ray | 8265 | Dashboard + job submission (NodePort 30265) |
| MLflow | 5000 | Experiment tracking, model registry |
| MinIO | 9000/9001 | S3-compatible artifact storage |
| PostgreSQL | 5432 | MLflow metadata backend |
| Prometheus | 9090 | Metrics collection |
| Grafana | 3000 | Dashboards (GPU, jobs, system) |

