# cortexgrid

`cortexgrid` is a Python library that connects your ML code to the deployed infrastructure. It wraps Ray, MLflow, and S3/MinIO so your training scripts don't need to know about URLs, credentials, or service endpoints.

### Installation

Add `cortexgrid` as a dependency in your project's `pyproject.toml`:

```toml
[project]
dependencies = [
    "cortexgrid",
]
```

Then `uv sync` to install it, and point it at the head's secrets server (reachable over the tailnet):

```bash
export CORTEXGRID_HEAD_URL=http://robolab-head:7700
```

### Usage

```python
import cortexgrid

cortexgrid.init(experiment="weather-forecast")
```

That single call reads the service URLs from the head's secrets server at `$CORTEXGRID_HEAD_URL` and connects to all services through them. It also creates (or finds) the named MLflow experiment and starts a new run inside it. If an experiment of that name was deleted (e.g. from the UI), a new experiment is created under the name: MLflow keeps a deleted experiment's name reserved, so the deleted one is renamed to `<name>__deleted__<id>` first (`delete_experiment` does that rename at deletion). Omit `experiment=` to auto-generate a unique name like `funky-koval-12`.

**One experiment per binary run.** `cortexgrid.init()` may only be called once per process. Every subsequent `cortexgrid.log_metric`, `cortexgrid.log_artifact`, checkpoint, and `cortexgrid.remote()` submission is scoped to that experiment+run. Remote jobs dispatched by the control plane inherit the experiment+run via the pickled payload, so their logging flows into the same MLflow run as the parent binary.

#### Experiment tracking (MLflow)

```python
cortexgrid.init(experiment="weather-forecast")

cortexgrid.log_params({"lr": 1e-3, "epochs": 20, "batch_size": 64})

for epoch in range(20):
    loss = train_one_epoch(model, dataloader)
    cortexgrid.log_metric("loss", loss, step=epoch)

    if epoch % 5 == 0:
        with cortexgrid.checkpoint() as ckpt:
            ckpt.epoch = epoch
            ckpt.save_training_state(model, optimizer)
```

No run-scoping context manager — `init()` starts the run, and every subsequent logging call flows into it. Metrics and artifacts are logged to the MLflow server on the DGX. View them at `http://<DGX_IP>:5000`.

#### Checkpointing and resuming

Inside a cortexgrid job, `cortexgrid.checkpoint()` returns an attribute-based checkpoint object that persists to MLflow artifacts when its `with` block exits. On job restart (either manual retry or `retry=True`), `cortexgrid.resume()` returns the last checkpoint for the same job ID, or `None` if there isn't one.

```python
ckpt = cortexgrid.resume()
if ckpt:
    ckpt.restore_training_state(model, optimizer)
    start_epoch = ckpt.epoch + 1
else:
    start_epoch = 0

for epoch in range(start_epoch, 20):
    train_one_epoch(model, dataloader)
    with cortexgrid.checkpoint() as ckpt:
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

job = cortexgrid.remote(train_step, batch, num_gpus=1, retry=True)
print(f"Submitted: {job.job_id}")
```

`cortexgrid.remote` submits a job *request* (a pickled payload plus a `JobLifecycle` record) to MLflow and returns a `JobFuture` immediately. It does not wait for the job to run or finish — use the UI at `http://<DGX_IP>:8000`, `job.status()`, or poll `cortexgrid.list_experiment_run_jobs(run_id)`, to observe status.

##### Blocking on the result

`JobFuture.result(timeout=None)` blocks until Ray reports the job terminal, then behaves like a local call: it returns whatever the function returned, or raises whatever the function raised.

```python
def score(batch) -> dict[str, float]:
    # runs on the DGX
    return {"loss": 0.12}

# Fire-and-forget — the form training uses. Nothing blocks, and a retry=True
# job cannot be waited on at all (see below).
training = cortexgrid.remote(train_step, batch, num_gpus=1, retry=True)
print(training.job_id, training.status().value)   # "blue-42 running"

# Blocking — the value comes back as if score() had run locally.
job = cortexgrid.remote(score, batch, num_gpus=1)
metrics = job.result()                            # {"loss": 0.12}

# ...or with a deadline. On TimeoutError the job keeps running; wait again later.
metrics = job.result(timeout=600)

# From another process — the handle is gone, the job id is enough.
metrics = cortexgrid.get_job_result(job_id, timeout=600)
```

Failures arrive as exceptions, not as a status to inspect:

```python
try:
    metrics = job.result()
except BadBatch as exc:                # exactly what score() raised on the DGX
    print(exc.__cause__)               # JobFailed, carrying the remote traceback
except cortexgrid.JobFailed:           # the job never got as far as returning
    ...                                # submission failed, driver died, or stopped
except cortexgrid.JobResultUnavailable:
    ...                                # it returned, but the value is not transportable
```

The driver cloudpickles the outcome to `job/{job_id}/result.pkl`, beside the payload manifest and the lifecycle, and the waiting side reads it back:

| Outcome | What `result()` does |
|---|---|
| The function returned | Returns its value |
| The function raised | Raises that exception, with the remote traceback attached as a chained `JobFailed` cause |
| The job never got as far as returning (submission failed, driver died, job stopped) | Raises `JobFailed` |
| The value or the exception did not survive cloudpickle | Raises `JobResultUnavailable` (value) or `JobFailed` (exception). A value that cannot be pickled never fails the job itself |
| `timeout` elapsed | Raises `TimeoutError`; the job keeps running |

Waiting on a `retry=True` job raises `ValueError`: retries are unbounded by design (see below), so the wait would have no end. Fire-and-forget submission is the form training uses — submit, then watch the UI.

A separate service — the **jobs control plane** — polls MLflow for pending job requests, matches them against the set of Ray submissions the cluster already has, and submits anything missing. It is also responsible for retrying failed jobs and honouring user-requested stops.

Each submission captures the code and dependencies the entry function needs automatically ([_bundle.py](https://github.com/robodatalab/cortexgrid/blob/main/cortexgrid/_bundle.py)):
- `bundle(entry)` traces the import graph from the function's source file, resolving each import the way the interpreter does (via `sys.path`). The standard library is excluded (it ships with the interpreter)
- Your own modules -- anything outside site-packages / dist-packages -- ship **as source**: they are staged at their import paths and tarred into the Ray `working_dir`
- Third-party packages are recorded as the installed distribution that owns the imported file, pinned to its installed version (`tqdm==4.67.3`), and Ray **pip-installs** them on the worker into a per-node cached virtualenv layered on the image (`runtime_env["pip"]`). An import into site-packages that no installed distribution owns fails `cortexgrid.remote` with `UnownedDependencyError`
- Distributions the worker image already has are not installed again: `worker_provides()` is the dependency closure of the packages baked into the ray image (torch and its CUDA stack, ray, mlflow, ...), and is subtracted from the pip list. See [k8s/docker/ray/Dockerfile](https://github.com/robodatalab/cortexgrid/blob/main/k8s/docker/ray/Dockerfile) and `_WORKER_BAKED` in `_bundle.py`, which must list the same packages
- Injects MLflow/S3 credentials so task code running on the DGX can reach all services

##### Retries

Pass `retry=True` and the control plane will resubmit the job whenever Ray reports the most recent attempt as `FAILED`. Retries are **unbounded by design**: the intended way to end a retry loop is to stop the job manually from the UI (which flips the `stop_requested` latch on the lifecycle, and the control plane stops the current Ray attempt on its next poll). This keeps the retry policy simple — you don't have to predict a good `max_retries` up front — and puts the human in the loop for anything that's failing persistently.

##### Stopping a job

```python
cortexgrid.stop_experiment_run_jobs(run_id)   # stops every job in the run
```

`stop_experiment_run_jobs` never touches Ray directly. It only flips `stop_requested` on each job's lifecycle record in MLflow. The control plane observes the flag on its next poll and calls `ray.stop_job` for any attempt that has reached Ray. For jobs that have not yet been submitted, the same flag short-circuits the submission path inside the worker.

#### Object storage (S3/MinIO)

```python
cortexgrid.upload("data/output.parquet", bucket="ray-checkpoints", key="run-42/output.parquet")
cortexgrid.download("ray-checkpoints", "run-42/output.parquet", local_path="./output.parquet")

# or get the raw boto3 client
s3 = cortexgrid.get_s3_client()
```

Works with MinIO on the DGX today, real S3 on AWS tomorrow — same code.

#### Getting raw clients

```python
mlflow_client = cortexgrid.get_mlflow_client()   # mlflow.tracking.MlflowClient
s3_client = cortexgrid.get_s3_client()           # boto3 S3 client
```

#### Model registry and serving

Save a trained model's weights together with the serve-app that fronts it, then deploy it as a Ray Serve application. A serve-app is a class fronted by a FastAPI app, marked with cortexgrid's `serve.ingress` (not Ray's):

```python
from cortexgrid import serve
from fastapi import FastAPI

app = FastAPI()

@serve.ingress(app)
class MyServeApp:
    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        self._weights_dir = cortexgrid.load_model(family, suffix, run_name)

    @app.post("/complete")
    async def complete(self, body: dict): ...

saved = cortexgrid.save_model(
    weights_dir, MyServeApp, family="qwen", suffix="instruct",
    # What one replica needs; the model is deployed only on a host that has it.
    requirements=cortexgrid.ModelRequirements(num_gpus=1, ram_gb=8, vram_gb=16),
)
deployed = cortexgrid.deploy_model("qwen", "instruct", saved.run_name, wait=True)
print(deployed.url)
```

The requirements are part of the model, not of the serve-app class: GPUs, RAM and VRAM (GiB, 0 meaning no requirement) are stored with it and matched against what the cluster's hosts have free. Among the hosts that fit, the model goes to the **smallest GPU** that does, so a 4 GiB model does not occupy a 128 GiB card a bigger one needs; it moves up only once the smaller cards are full. `num_gpus` may be a fraction (`0.25`) to share one card between models, in which case `vram_gb` is what keeps them from overcommitting it. Correct them later with `cortexgrid.set_model_requirements(family, suffix, run_name, requirements)` or on the model card in the dashboard; `cortexgrid.deploy_model(..., num_replicas=2)` chooses how many copies to run.

Anything else the serve-app has to know about the model - which model a provider should be asked for, an endpoint, the name of a secret to read - goes in a free-form string mapping on the same entry: `save_model(..., config={"model": "claude-opus-5"})`. The serve-app reads it in `__init__` with `cortexgrid.model_config(family, suffix, run_name)`; `cortexgrid.set_model_config(family, suffix, run_name, config)` or the model card replaces it. cortexgrid stores the mapping without interpreting it, and a tag is readable by anyone with registry access, so a credential belongs in `set_secret` with only its name in the config.

`save_model` saves a new copy under every run - meant for weights the run produced (e.g. a fine-tune). For a model produced elsewhere (e.g. a pretrained base model), `cortexgrid.import_model(source, MyServeApp, family, suffix)` uploads it once under `run_name=cortexgrid.IMPORTED` and on later runs only re-bundles `MyServeApp` if its code changed; deploy it with `deploy_model(family, suffix, cortexgrid.IMPORTED)`. A model with no weights to stage - one behind a provider's API, e.g. Gemini or OpenAI - is registered the same way by `cortexgrid.register_model(MyServeApp, family, suffix, config=...)`: same key, same reuse, only the bundle stored. [Serving a hosted-API model](https://github.com/robodatalab/cortexgrid/blob/main/docs/cortexgrid/model-serving.md#serving-a-hosted-api-model) walks through one end to end - serve-app, API key, registration, deploy.

`save_model` is synchronous (registry lifecycle: `uploading` -> `ready`); `deploy_model` schedules the serving lifecycle (`deploying` -> `running`). With `wait=True` a failed deploy raises `cortexgrid.ModelDeployFailed`; `cortexgrid.wait_for_model_serving(family, suffix, run_name, timeout=...)` waits on a deploy started elsewhere, and re-deploying a failed model retries it from scratch. See [model-serving.md](https://github.com/robodatalab/cortexgrid/blob/main/docs/cortexgrid/model-serving.md) for both lifecycles end to end - upload/deploy/undeploy/delete, status queries (`model_registry_status`, `model_serving_status`), and error handling.

### API reference

| Function | Description |
|----------|-------------|
| `cortexgrid.init(experiment=None)` | Configure connections + start a new MLflow run inside the named experiment. One call per binary. |
| `cortexgrid.log_metric(key, value, step)` | Log a metric |
| `cortexgrid.log_metrics(metrics, step)` | Log multiple metrics |
| `cortexgrid.log_params(params)` | Log parameters |
| `cortexgrid.log_artifact(path, artifact_path)` | Log a file as an artifact |
| `cortexgrid.checkpoint()` | Context manager returning an attribute-based checkpoint saved to MLflow on exit |
| `cortexgrid.resume()` | Load the latest checkpoint for the current job, or `None` |
| `cortexgrid.remote(fn, *args, num_gpus=0, num_cpus=1, retry=False, **kwargs)` | Submit a function to the jobs control plane; returns a `JobFuture` |
| `JobFuture.status()` / `.done()` / `.result(timeout=None)` | Live status of a submitted job, and its function's return value (blocking) |
| `cortexgrid.get_job_result(job_id, timeout=None)` | Block on a job of the current run by id; returns its value or raises its exception |
| `cortexgrid.list_experiment_run_jobs(run_id)` | List `JobLifecycle` records for every cortexgrid job in a run |
| `cortexgrid.stop_experiment_run_jobs(run_id)` | Request every job in a run to stop (flips the `stop_requested` latch) |
| `cortexgrid.get_ray_job_id_for_cortexgrid_job(run_id, job_id)` | Ray submission id of a cortexgrid job's latest attempt, or `None` |
| `cortexgrid.get_ray_job_status(ray_job_id)` | Live Ray status for a submission id |
| `cortexgrid.get_ray_logs(ray_job_id)` | Tail the stdout/stderr of a Ray submission |
| `cortexgrid.upload(path, bucket, key)` | Upload a file to S3/MinIO |
| `cortexgrid.download(bucket, key, path)` | Download a file from S3/MinIO |
| `cortexgrid.get_mlflow_client()` | Raw configured MLflow client |
| `cortexgrid.get_s3_client()` | Raw configured boto3 S3 client |

## ML compute stack

The DGX Spark runs the following services as k8s workloads managed by Argo CD (see [../k8s/argo_deployments/](https://github.com/robodatalab/cortexgrid/tree/main/k8s/argo_deployments/)):

| Service | Port | Purpose |
|---------|------|---------|
| Ray | 8265 | Dashboard + job submission (NodePort 30265) |
| MLflow | 5000 | Experiment tracking, model registry |
| MinIO | 9000/9001 | S3-compatible artifact storage |
| PostgreSQL | 5432 | MLflow metadata backend |
| Prometheus | 9090 | Metrics collection |
| Grafana | 3000 | Dashboards (GPU, jobs, system) |

