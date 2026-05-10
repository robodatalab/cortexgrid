# Model serving

> **Status (2026-05-10):** cortexflow side implemented. The downstream piece in [model-gateway](https://github.com/robolab/model-gateway) (the `CortexflowModel` deployment class and the `RobolabModel` HTTP client) is **pending**.

cortexflow has two model-related surfaces:

- **Model registry** ([cortexflow.model_storage](../../cortexflow/model_storage.py)) - persists trained models to MinIO + MLflow Model Registry, addressable as `(family, suffix, run_name)`.
- **Model serving** ([cortexflow.model_serving](../../cortexflow/model_serving.py)) - schedules a Ray Serve app that loads a model from the registry on startup.

Both speak the same `(family, suffix, run_name)` triple; the serving side calls `cortexflow.load_model` internally.

## Quick start

End-to-end flow from a training script: save a model, deploy it, hit it.

```python
import cortexflow
import tempfile
from pathlib import Path

cortexflow.Experiment.init("my-experiment")

# 1. Save a model the registry. The current Experiment supplies run_name.
with tempfile.TemporaryDirectory() as d:
    model.save_pretrained(d)         # HF: writes config.json + safetensors + tokenizer
    cortexflow.save_model(d, suffix="instruct", family="Qwen2-2.5B")

# 2. Deploy on the cluster (one Ray Serve app per (family, suffix, run_name)).
deployment = cortexflow.deploy_model(
    YourDeploymentClass,             # see below for what this looks like
    family="Qwen2-2.5B",
    suffix="instruct",
    run_name=cortexflow.Experiment.get_instance().run_name(),
)
print(deployment.url)                # http://<head>:30000/r/Qwen2-2.5B/instruct/<run_name>

# 3. Hit it.
import requests
response = requests.post(f"{deployment.url}/complete", json={...})

# 4. Tear down.
cortexflow.undeploy_model("Qwen2-2.5B", "instruct", "<run_name>")
```

`cortexflow.deploy_model` requires a Ray cluster connection. From inside the cluster (Ray jobs) it works automatically; from a laptop, set `RAY_ADDRESS=ray://<head>:30001` in the environment.

## Writing a deployment class

The deployment class is your code; cortexflow only wraps it for Ray Serve. Skeleton:

```python
from fastapi import FastAPI
from ray import serve
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

import cortexflow

_app = FastAPI()


@cortexflow.model_deployment(num_gpus=1, num_replicas=1)
@serve.ingress(_app)
class MyHuggingFaceModel:
    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        path = cortexflow.load_model(family, suffix, run_name)   # downloads weights from MinIO
        self._tokenizer = AutoTokenizer.from_pretrained(path)
        self._model = AutoModelForCausalLM.from_pretrained(path, torch_dtype=torch.float16)

    @_app.post("/complete")
    async def complete(self, body: dict) -> dict:
        ...
```

Key points:

- `__init__` receives the same `(family, suffix, run_name)` triple you pass to `deploy_model`. Use it to call `cortexflow.load_model(...)`, which returns a local `Path` to the downloaded weights.
- The Ray Serve replica must have your class importable. For a custom image, install whatever package exports the class. For ad-hoc tests, putting the class in a module that's part of `cortexflow.remote()`'s working_dir (e.g. under `tests/`) is enough.
- `@cortexflow.model_deployment(...)` is a thin wrapper around `@serve.deployment(...)` that fills in cortexflow defaults. You can pass through any `serve.deployment` kwarg.

## API

All exported from `cortexflow.*`.

### Registry

| Function | Purpose |
|----------|---------|
| `save_model(model_dir, suffix, family) -> SavedModel` | Upload `model_dir` and create a new MLflow `ModelVersion`. Uses the current Experiment's run_id/run_name. |
| `load_model(family, suffix, run_name, dest_dir=None) -> Path` | Download weights to a local directory. Returns the local path. |
| `list_models() -> list[SavedModel]` | Every `ModelVersion` in the registry, mapped to a `SavedModel` record. |
| `delete_model(family, suffix, run_name)` | Drop the `ModelVersion` and its blobs in S3. |

`SavedModel` is a dataclass: `family`, `suffix`, `run_name`, `created_at`, `data_blob_path`.

### Serving

| Function | Purpose |
|----------|---------|
| `model_deployment(num_gpus=1, num_replicas=1, **kw)` | Decorator: wraps a class as a Ray Serve deployment with cortexflow defaults. |
| `deploy_model(cls, family, suffix, run_name) -> Deployment` | Schedule a Ray Serve app. Idempotent in the sense that `serve.run` replaces an existing app with the same name. |
| `undeploy_model(family, suffix, run_name)` | Tear down the Ray Serve app for this model. |
| `list_deployed_models() -> list[Deployment]` | Every active Ray Serve app whose name matches our scheme. |

`Deployment` is a dataclass: `family`, `suffix`, `run_name`, `url`, `status`.

The Ray Serve app name is `<family>__<suffix>__<run_name>`; the route prefix is `/r/<family>/<suffix>/<run_name>`. Family/suffix/run_name must not contain `/` or `__`.

## How it fits together

```
laptop / training job
  |
  | cortexflow.save_model(dir, suffix, family)
  v
MinIO  s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/...   <- bytes
MLflow Model Registry                                                   <- ModelVersion(name=family/suffix, tags={family, suffix, run_name}, source=s3://...)

  |
  | cortexflow.deploy_model(cls, family, suffix, run_name)
  v
Ray Serve replica on the cluster
  __init__ -> cortexflow.load_model(family, suffix, run_name)  # pulls weights from MinIO
  exposes /r/<family>/<suffix>/<run_name>/<your-routes>

  |
  | requests.post(deployment.url + "/...", ...)
  v
  HTTP traffic via tailscale, NodePort 30000 on the cluster head
```

Cleanup cascades: `cortexflow.delete_run(run_id)` (and therefore `delete_experiment`) calls `delete_models_for_run`, which deletes every `ModelVersion` linked to that run plus the corresponding `models/<run_name>/` prefix in S3. Active Ray Serve deployments are *not* torn down by `delete_run` - call `undeploy_model` explicitly.

## Dependencies

Already deployed in the cluster:
- Ray + Ray Serve ([k8s/workloads/ray/](../../k8s/workloads/ray/), with port 8000 exposed at NodePort 30000)
- MLflow + MinIO ([k8s/workloads/mlflow/](../../k8s/workloads/mlflow/), [k8s/workloads/minio/](../../k8s/workloads/minio/))
- Tailscale ([k8s/workloads/tailscale-operator/](../../k8s/workloads/tailscale-operator/))

cortexflow secrets used:
- `RAY_SERVE_URI` - data plane (port 30000), used to compose the deployment URL
- Existing `MLFLOW_TRACKING_URI`, `S3_*` for the registry side
- Optional `RAY_ADDRESS` env var (laptop only) for `deploy_model`/`undeploy_model` to reach the cluster

## Pending work

- **`CortexflowModel` deployment class** in model-gateway: an off-the-shelf class implementing the HF inference path so most users don't need to write their own. Should be importable as `model_gateway.providers.cortexflow.CortexflowModel`.
- **`RobolabModel` HTTP client** in model-gateway: a `model_gateway.deploy_model("robolab:<family>/<suffix>/<run_name>")` provider that returns a `CompletingModel` client over the deployment URL. Closes the loop so application code never has to touch cortexflow directly to *use* a deployed model.
- **Tear-down policy** for idle deployments. Today only explicit `undeploy_model` releases the GPU; consider an idle eviction policy when the registry has more deployable runs than cluster GPUs.

## Alternatives that were considered

| Topic | Picked | Rejected | Reason |
|---|---|---|---|
| Serving runtime | Ray Serve | vLLM | vLLM is a second serving stack with patchy arm64; revisit when LLM throughput is a measured problem. |
| Weights source | MLflow Model Registry + direct MinIO writes | MLflow run artifacts only | Listing speed: registry filtering is O(1) where artifact-walking was O(N) with one GET per manifest. |
| Library shape | cortexflow + model-gateway separate | merged | Keeps cortexflow torch-free for laptop callers; model-gateway stays reusable as a generic LLM client. |
| Endpoint discovery | static URL composed from `RAY_SERVE_URI` + route prefix | jobs-control-plane lookup, MagicDNS, MLflow tag | URL is fully determined by `(family, suffix, run_name)`; no extra state to keep in sync. |
