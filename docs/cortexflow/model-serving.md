# Model serving

> **Status (2026-05-11):** redesign in flight. The current implementation makes the caller a Ray driver (`ray.init(address="auto")` inside `deploy_model`), which only works on Ray nodes / inside Ray jobs - it breaks for any other caller, including the arc-runner pods used by CI. This doc describes the **target design**: caller stays HTTP-only, the Ray cluster is responsible for driving Serve apps. Downstream `CortexflowModel`/`RobolabModel` in model-gateway is still pending.

cortexflow has two model-related surfaces:

- **Model registry** ([cortexflow.model_storage](../../cortexflow/model_storage.py)) - persists trained models to MinIO + MLflow Model Registry, addressable as `(family, suffix, run_name)`.
- **Model serving** ([cortexflow.model_serving](../../cortexflow/model_serving.py)) - schedules a Ray Serve app that loads a model from the registry on startup.

Both speak the same `(family, suffix, run_name)` triple; the serving side calls `cortexflow.load_model` internally.

## Design principle: caller is not the driver

The caller of `deploy_model`/`undeploy_model`/`list_deployed_models` never connects to Ray as a driver. It only speaks HTTP to the Ray dashboard (port 30265 / `RAY_JOB_SERVER_URI`). This mirrors how [cortexflow.remote](../../cortexflow/__init__.py) submits jobs: the caller's job is to describe the work; turning that description into running Ray actors is the cluster's job.

Concretely:

- `submit_ray_job` (existing) uses `JobSubmissionClient` -> dashboard `/api/jobs/` REST endpoints.
- `deploy_model` (new) uses the dashboard's `/api/serve/applications/` REST endpoints (PUT to deploy, DELETE to remove, GET to list). Ray's Serve controller on the cluster materialises the application.

No `ray.init` anywhere in cortexflow.model_serving.

## Quick start

End-to-end flow from a training script: save a model, deploy it, hit it.

```python
import cortexflow
import tempfile

cortexflow.Experiment.init("my-experiment")

# 1. Save a model to the registry. The current Experiment supplies run_name.
with tempfile.TemporaryDirectory() as d:
    model.save_pretrained(d)         # HF: writes config.json + safetensors + tokenizer
    cortexflow.save_model(d, suffix="instruct", family="Qwen2-2.5B")

# 2. Deploy on the cluster (one Ray Serve app per (family, suffix, run_name)).
deployment = cortexflow.deploy_model(
    YourDeploymentClass,             # see "Writing a deployment class" below
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

No `RAY_ADDRESS` setup, no Ray Client. The call goes out over HTTP to `RAY_JOB_SERVER_URI` (already in SM, already used by `cortexflow.remote`); the cluster handles the rest.

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
- The Ray Serve replica must have your class importable. See [Code delivery](#code-delivery) below for how the class reaches the cluster.
- `@cortexflow.model_deployment(...)` is a thin wrapper around `@serve.deployment(...)` that fills in cortexflow defaults. You can pass through any `serve.deployment` kwarg.

## Code delivery

`deploy_model(cls, family, suffix, run_name)` takes the deployment class directly. cortexflow handles everything in between:

1. Walk the class's import graph - same logic [_bundle.py](../../cortexflow/_bundle.py) already does for `cortexflow.remote`, generalised to accept a class entry point (currently function-only).
2. Stage the in-workspace files into a tempdir and capture external pip deps via `pip freeze` + `filter_pip_freeze`.
3. Upload the staging dir as a zip to MinIO under `s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip`.
4. PUT `/api/serve/applications/` with:
   ```json
   {
     "name": "<family>__<suffix>__<run_name>",
     "route_prefix": "/r/<family>/<suffix>/<run_name>",
     "import_path": "<cls.__module__>:<cls.__name__>",
     "args": {"family": "...", "suffix": "...", "run_name": "..."},
     "runtime_env": {
       "working_dir": "s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip",
       "pip": [/* filtered pip freeze */]
     },
     "deployments": [/* num_replicas, ray_actor_options from @cortexflow.model_deployment */]
   }
   ```

Why not bake the class into the cluster image? cortexflow doesn't own deployment classes - they live downstream (e.g. in `model-gateway`, or in user training code). The image cortexflow ships is generic; we don't know in advance which classes will ever serve.

Why not attach code to the model at save_model time? Considered: it would tie a model artifact to a specific serving class, giving stronger reproducibility. Rejected: callers may want to deploy the same weights with a new wrapper (bug-fixed routing layer, different inference path) without re-saving. Decoupling save from serve keeps that flexibility - the cost is that the caller is responsible for keeping their serving class compatible with previously-saved weights.

Weights are never shipped via `runtime_env` - they stay in MinIO and the replica's `__init__` downloads them via `cortexflow.load_model`. The bundler only ships *code*.

### Bundle lifecycle

Serve bundles in MinIO under `serve-bundles/<run_name>/...` are cleaned up by `delete_run` alongside the run's weights prefix. Stale bundles from undeployed-but-not-deleted runs are not currently garbage-collected; if that becomes a problem, the cleanest signal is "no Serve application currently references this bundle URL" - implement at that point, not before.

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
| `deploy_model(cls, family, suffix, run_name) -> Deployment` | Schedule a Ray Serve app. cortexflow bundles `cls`'s code + pip deps, uploads to MinIO, and PUTs `/api/serve/applications/`. Idempotent on `(family, suffix, run_name)` because the application name is deterministic. |
| `undeploy_model(family, suffix, run_name)` | DELETE the Ray Serve app for this model. |
| `list_deployed_models() -> list[Deployment]` | GET `/api/serve/applications/` and project to records whose name matches our scheme. |

`Deployment` is a dataclass: `family`, `suffix`, `run_name`, `url`, `status`.

The Ray Serve app name is `<family>__<suffix>__<run_name>`; the route prefix is `/r/<family>/<suffix>/<run_name>`. Family/suffix/run_name must not contain `/` or `__`.

## How it fits together

```
caller (laptop / arc-runner / training job)
  |
  | cortexflow.save_model(dir, suffix, family)
  v
MinIO  s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/...   <- bytes
MLflow Model Registry                                                   <- ModelVersion(name=family/suffix, tags={family, suffix, run_name}, source=s3://...)

caller
  |
  | cortexflow.deploy_model(cls, family, suffix, run_name)
  |   1. stage_bundle(cls) -> tempdir + filtered pip freeze
  |   2. upload zip to s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip
  v
HTTP PUT  RAY_JOB_SERVER_URI/api/serve/applications/    <- application spec
                                                          { name, route_prefix,
                                                            import_path, args,
                                                            runtime_env, deployments }
  |
  v
Ray Serve controller on the cluster
  schedules replica(s) on Ray workers per ray_actor_options
  replica __init__ runs cortexflow.load_model(...)  # pulls weights from MinIO
  exposes /r/<family>/<suffix>/<run_name>/<your-routes>

caller
  |
  | requests.post(deployment.url + "/...", ...)
  v
  HTTP traffic via tailscale, NodePort 30000 on the cluster head
```

Cleanup cascades: `cortexflow.delete_run(run_id)` (and therefore `delete_experiment`) calls `delete_models_for_run`, which deletes every `ModelVersion` linked to that run plus the corresponding `models/<run_name>/` prefix in S3 and any `serve-bundles/<run_name>/` prefix used for staged code. Active Ray Serve deployments are *not* torn down by `delete_run` - call `undeploy_model` explicitly.

## Dependencies

Already deployed in the cluster:
- Ray + Ray Serve ([k8s/workloads/ray/](../../k8s/workloads/ray/), with port 8000 exposed at NodePort 30000)
- MLflow + MinIO ([k8s/workloads/mlflow/](../../k8s/workloads/mlflow/), [k8s/workloads/minio/](../../k8s/workloads/minio/))
- Tailscale ([k8s/workloads/tailscale-operator/](../../k8s/workloads/tailscale-operator/))

cortexflow secrets used:
- `RAY_JOB_SERVER_URI` - dashboard endpoint (port 30265); `deploy_model`/`undeploy_model`/`list_deployed_models` PUT/DELETE/GET against `/api/serve/applications/` here. Same secret `cortexflow.remote` already uses.
- `RAY_SERVE_URI` - data plane (port 30000), used to compose the deployment URL returned to callers.
- Existing `MLFLOW_TRACKING_URI`, `S3_*` for the registry side and (under path 3) for staging bundles.
- No `RAY_ADDRESS` anywhere - the caller does not become a Ray driver.

## Pending work

- **REST-based rewrite of `model_serving.py`** (this design). Replace the three `_connect()` callers with REST calls against `/api/serve/applications/`. Move any Ray dashboard plumbing into [cortexflow.ray_util](../../cortexflow/ray_util.py) so it parallels `JobSubmissionClient`. Delete `_connect`, drop the `ray.init` dependency from cortexflow.model_serving.
- **Serve-bundle staging.** Generalise `_bundle.py` to accept a class entry point (currently function-only). Upload the staging dir as a zip to MinIO under `serve-bundles/<run_name>/<family>__<suffix>.zip` and pass the resulting `s3://` URL as `runtime_env.working_dir`. The integration test (`tests/integration/cortexflow/test_model_serving.py`) is the first consumer.
- **`CortexflowModel` deployment class** in model-gateway: an off-the-shelf class implementing the HF inference path so most users don't need to write their own. Should be importable as `model_gateway.providers.cortexflow.CortexflowModel`. Even when it lands, it still flows through the bundler - cortexflow has no shortcut path that skips bundling.
- **`RobolabModel` HTTP client** in model-gateway: a `model_gateway.deploy_model("robolab:<family>/<suffix>/<run_name>")` provider that returns a `CompletingModel` client over the deployment URL. Closes the loop so application code never has to touch cortexflow directly to *use* a deployed model.
- **Tear-down policy** for idle deployments. Today only explicit `undeploy_model` releases the GPU; consider an idle eviction policy when the registry has more deployable runs than cluster GPUs.

## Alternatives that were considered

| Topic | Picked | Rejected | Reason |
|---|---|---|---|
| Serving runtime | Ray Serve | vLLM | vLLM is a second serving stack with patchy arm64; revisit when LLM throughput is a measured problem. |
| Weights source | MLflow Model Registry + direct MinIO writes | MLflow run artifacts only | Listing speed: registry filtering is O(1) where artifact-walking was O(N) with one GET per manifest. |
| Library shape | cortexflow + model-gateway separate | merged | Keeps cortexflow torch-free for laptop callers; model-gateway stays reusable as a generic LLM client. |
| Endpoint discovery | static URL composed from `RAY_SERVE_URI` + route prefix | jobs-control-plane lookup, MagicDNS, MLflow tag | URL is fully determined by `(family, suffix, run_name)`; no extra state to keep in sync. |
| Caller -> cluster transport | Serve REST API (`/api/serve/applications/`) | (a) `ray.init` + `serve.run` in caller; (b) submit a Ray job that calls `serve.run` | (a) makes the caller a Ray driver - works on Ray nodes / jobs only, breaks on arc-runners; (b) decouples application lifetime from a job lifetime, then we'd have to babysit the job. REST keeps cortexflow.model_serving HTTP-only and parallels how `cortexflow.remote` talks to Ray Jobs. |
| Code delivery | Bundle the class at deploy time, upload to MinIO, pass via `runtime_env.working_dir` | (a) bake class into cluster image; (b) attach code to the model at `save_model` time | (a) cortexflow doesn't own deployment classes - they live downstream; baking would invert the dependency. (b) couples weights and serving class; we want to be able to redeploy old weights under a new wrapper without re-saving. |
