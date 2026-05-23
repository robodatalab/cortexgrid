# Model serving

cortexflow has two model-related surfaces:

- **Model registry** ([cortexflow.model_storage](../../cortexflow/model_storage.py)) - persists trained models to S3 + MLflow Model Registry, addressable as `(family, suffix, run_name)`. At save time it also bundles the model's *class* (code + pip deps) so the cluster can reconstruct it later.
- **Model serving** ([cortexflow.model_serving](../../cortexflow/model_serving.py)) - schedules a Ray Serve app that reloads the model from the registry on startup and exposes a single `POST /infer` endpoint.

Both speak the same `(family, suffix, run_name)` triple.

## Design principle: caller is not the driver

The caller of `save_model`/`deploy_model`/`undeploy_model`/`list_deployed_models` never connects to Ray as a driver. It only speaks HTTP to the Ray dashboard (`RAY_JOB_SERVER_URI`, `/api/serve/applications/`). This mirrors how [cortexflow.remote](../../cortexflow/__init__.py) submits jobs: the caller describes the work; turning that into running Ray actors is the cluster's job.

Concretely:

- `submit_ray_job` uses `JobSubmissionClient` -> dashboard `/api/jobs/`.
- `deploy_model` uses the dashboard's `/api/serve/applications/` (PUT to deploy, GET to list). Ray's Serve controller materialises the application.

No `ray.init` anywhere in cortexflow.model_serving.

## The `Model` abstraction

User code subclasses [cortexflow.Model](../../cortexflow/model.py) and implements three methods:

```python
import cortexflow
from pathlib import Path


class MyModel(cortexflow.Model):
    num_gpus = 1        # class attrs configure the Ray Serve actor
    num_replicas = 1

    def __init__(self, weights):
        self._weights = weights

    def infer(self, prompt: str) -> str:
        ...

    def save(self, d: Path) -> None:
        # write weights/config under d/
        ...

    @classmethod
    def load(cls, d: Path) -> "MyModel":
        # reconstruct from the directory `save` wrote
        ...
```

The same class works both locally and on the cluster:

- **Local:** `m = MyModel(...); m.infer(x)`.
- **Remote:** `cortexflow.save_model(m, family, suffix)`, then `deployed = cortexflow.deploy_model(family, suffix, run_name)`, then `deployed.infer(x)`. The deployed proxy POSTs `(*args, **kwargs)` to `/infer` and returns the decoded result.

There is no decorator and no FastAPI in user code - the wrapping lives in [cortexflow._serve_entry](../../cortexflow/_serve_entry.py), which exposes the single generic `POST /infer` route.

## Quick start

```python
import cortexflow

cortexflow.Experiment.init("my-experiment")

# 1. Save: uploads weights AND bundles the class to S3.
m = MyModel(...)
saved = cortexflow.save_model(m, family="qwen", suffix="instruct")

# 2. Deploy on the cluster. No class object needed; cortexflow re-imports it
#    from the import path it captured at save time.
deployed = cortexflow.deploy_model(
    family="qwen",
    suffix="instruct",
    run_name=cortexflow.Experiment.get_instance().run_name(),
    wait=True,                       # block until app is RUNNING
)
print(deployed.url)                  # http://<head>:30000/r/qwen/instruct/<run_name>

# 3. Hit it.
result = deployed.infer("hello")

# 4. Tear down.
cortexflow.undeploy_model("qwen", "instruct", "<run_name>")
```

No `RAY_ADDRESS`, no Ray Client. The calls go out over HTTP to `RAY_JOB_SERVER_URI` (already in SM, already used by `cortexflow.remote`); the cluster handles the rest.

## Code delivery: bundle at save time

`save_model(model, family, suffix)` does two things:

1. **Weights:** drives `model.save(d)` into a tempdir, uploads to `s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/`, and records that path as the MLflow `ModelVersion.source`.
2. **Class bundle:** walks the model class's import graph (same logic [_bundle.py](../../cortexflow/_bundle.py) uses for `cortexflow.remote`, generalised to accept a class entry point), captures external pip deps via `pip freeze` + `filter_pip_freeze`, zips the staging dir, and uploads to `s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip`.

The bundle URL, the class import path, and the filtered pip list are persisted as MLflow tags on the new `ModelVersion`:

| Tag | Meaning |
|---|---|
| `serve_bundle_url` | `s3://...` URL of the zipped staging dir |
| `class_import_path` | `<module>:<ClassName>` to import on the replica |
| `serve_pip_list_json` | JSON-encoded `runtime_env.pip` entries |

`deploy_model` reads those tags back; it does not need the class object, so deployment can happen from any environment that can hit MLflow + the Ray dashboard.

Why bundle at save time (rather than at deploy time)? It keeps `deploy_model` callers stateless - you can deploy a model from a different process / repo / machine than the one that trained it, with only `(family, suffix, run_name)` in hand. The price is that the class must be importable in the caller of `save_model`; that is true by construction since `save_model` takes an instance.

Weights are never shipped via `runtime_env` - they stay in S3 and the replica's `load_model` call downloads them on `__init__`. The bundler ships *code* only.

### Bundle lifecycle

`delete_model(family, suffix, run_name)` removes the `ModelVersion`, the weights prefix, and the serve bundle. `delete_models_for_run(run_id)` (called by `delete_run` and therefore `delete_experiment`) removes every `ModelVersion` linked to the run, the `models/<run_name>/` prefix, and the `serve-bundles/<run_name>/` prefix. Active Ray Serve deployments are *not* torn down by `delete_run` - call `undeploy_model` explicitly.

## On the replica

`deploy_model` PUTs an application spec whose `import_path` is the generic builder [cortexflow._serve_entry:build](../../cortexflow/_serve_entry.py). On the replica, `build`:

1. Imports the user's class from `class_import_path`.
2. Reads `num_gpus` / `num_replicas` class attrs and applies them via `.options(...)` on the wrapper deployment.
3. Binds `_InferenceWrapper(family, suffix, run_name)`. The wrapper's `__init__` calls `cortexflow.load_model(family, suffix, run_name)` to reconstruct the model (which in turn calls `cls.load(weights_dir)` on a freshly downloaded tempdir).
4. Exposes a single `POST /infer` that forwards `body["args"]` / `body["kwargs"]` to `model.infer(...)` and returns `{"result": ...}`.

The `DeployedModel` proxy returned to the caller wraps this with `.infer(*args, **kwargs)` and the deployment URL.

## API

All exported from `cortexflow.*`.

### Registry

| Function | Purpose |
|----------|---------|
| `save_model(model, family, suffix) -> SavedModel` | Save weights via `model.save(d)`, bundle `type(model)` and its pip deps, register a new MLflow `ModelVersion`. Uses the current Experiment's `run_id`/`run_name`. |
| `load_model(family, suffix, run_name) -> Model` | Re-import the class from MLflow tags, download weights to a tempdir, and return `cls.load(tempdir)`. The tempdir is removed once `load` returns, so the implementation must read everything it needs during the call. |
| `list_models() -> list[SavedModel]` | Every `ModelVersion` in the registry, mapped to a `SavedModel`. |
| `delete_model(family, suffix, run_name)` | Drop the `ModelVersion`, the weights blob, and the serve bundle. |

`SavedModel`: `family`, `suffix`, `run_name`, `created_at`, `data_blob_path`, `size_bytes`.

### Serving

| Function | Purpose |
|----------|---------|
| `deploy_model(family, suffix, run_name, wait=False) -> DeployedModel` | Read bundle metadata from MLflow, PUT the Serve app spec. Idempotent on `(family, suffix, run_name)`: the app name is deterministic, so a re-PUT replaces. With `wait=True`, blocks until the controller reports the app `RUNNING` (5 min cap). `DEPLOY_FAILED` raises; timing out raises. |
| `undeploy_model(family, suffix, run_name)` | Re-PUT the applications list with this app removed. |
| `list_deployed_models() -> list[Deployment]` | GET `/api/serve/applications/` and return records whose name matches `<family>__<suffix>__<run_name>`. |

`DeployedModel`: `url` plus `.infer(*args, **kwargs)` HTTP proxy.
`Deployment`: `family`, `suffix`, `run_name`, `url`, `status`.

The Ray Serve app name is `<family>__<suffix>__<run_name>`; the route prefix is `/r/<family>/<suffix>/<run_name>`. `family`, `suffix`, `run_name` must not contain `/` or `__`.

## Application spec PUT to Ray

```json
{
  "name": "<family>__<suffix>__<run_name>",
  "route_prefix": "/r/<family>/<suffix>/<run_name>",
  "import_path": "cortexflow._serve_entry:build",
  "args": {
    "class_import_path": "<module>:<ClassName>",
    "family": "...", "suffix": "...", "run_name": "..."
  },
  "runtime_env": {
    "working_dir": "s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip",
    "pip": ["--extra-index-url https://download.pytorch.org/whl/cu128", "..."]
  }
}
```

Replica options (`num_replicas`, `ray_actor_options.num_gpus`) are not in the spec - `_serve_entry.build` applies them via `.options(...)` on the wrapper after reading the user class's class attrs.

`deploy_model` reconstructs the full applications list (GET, replace this entry, PUT) because `/api/serve/applications/` is declarative: the PUT body is the desired complete set.

## How it fits together

```
caller (laptop / arc-runner / training job)
  |
  | cortexflow.save_model(model, family, suffix)
  |   1. model.save(d) into tempdir, upload to s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/
  |   2. bundle_class(type(model)) -> zip to s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip
  |   3. MLflow create_model_version(name="<family>__<suffix>", source=..., tags={family, suffix, run_name, serve_bundle_url, class_import_path, serve_pip_list_json, size_bytes})
  v
S3 (weights + zipped bundle)
MLflow Model Registry (ModelVersion + tags)

caller
  |
  | cortexflow.deploy_model(family, suffix, run_name, wait=True)
  |   1. read bundle metadata from MLflow tags
  |   2. PUT /api/serve/applications/  (full applications list)
  v
Ray Serve controller on the cluster
  fetches the bundle zip via runtime_env.working_dir
  installs runtime_env.pip
  imports cortexflow._serve_entry:build, which re-imports the user class
  binds _InferenceWrapper -> __init__ calls cortexflow.load_model(...)
  exposes /r/<family>/<suffix>/<run_name>/infer

caller
  |
  | deployed.infer(*args, **kwargs)  ->  POST {url}/infer  {"args": [...], "kwargs": {...}}
  v
HTTP traffic via tailscale, NodePort 30000 on the cluster head
```

## Dependencies

Already deployed in the cluster:
- Ray + Ray Serve ([k8s/workloads/ray/](../../k8s/workloads/ray/), with port 8000 exposed at NodePort 30000)
- MLflow + MinIO ([k8s/workloads/mlflow/](../../k8s/workloads/mlflow/), [k8s/workloads/minio/](../../k8s/workloads/minio/))
- Tailscale ([k8s/workloads/tailscale_operator/](../../k8s/workloads/tailscale_operator/))

cortexflow secrets used:
- `RAY_JOB_SERVER_URI` - dashboard endpoint; `deploy_model`/`undeploy_model`/`list_deployed_models` PUT/GET against `/api/serve/applications/` here. Same secret `cortexflow.remote` already uses.
- `RAY_SERVE_URI` - data plane (port 30000), used to compose the deployment URL returned to callers.
- `GH_TOKEN` - injected into `git+https://github.com/...` entries in `runtime_env.pip` so the replica can `pip install` private deps.
- Existing `MLFLOW_TRACKING_URI`, `S3_*` for the registry side and for staging bundles.
- No `RAY_ADDRESS` - the caller does not become a Ray driver.

## Pending work

- **`infer` schema.** The proxy posts `{"args": [...], "kwargs": {...}}` and the wrapper passes them through, but everything inside must be JSON-serialisable. Larger / typed payloads (tensors, images, streaming) are not yet handled.
- **Tear-down policy** for idle deployments. Today only explicit `undeploy_model` releases the GPU; consider an idle eviction policy when the registry has more deployable runs than cluster GPUs.
- **Stale-bundle GC.** Bundles for undeployed-but-not-deleted runs are not currently garbage-collected. If it becomes a problem, the cleanest signal is "no Serve application currently references this bundle URL"; implement at that point, not before.

## Alternatives that were considered

| Topic | Picked | Rejected | Reason |
|---|---|---|---|
| Serving runtime | Ray Serve | vLLM | vLLM is a second serving stack with patchy arm64; revisit when LLM throughput is a measured problem. |
| Weights source | MLflow Model Registry + direct S3 writes | MLflow run artifacts only | Listing speed: registry filtering is O(1) where artifact-walking was O(N) with one GET per manifest. |
| Library shape | cortexflow + model-gateway separate | merged | Keeps cortexflow torch-free for laptop callers; model-gateway stays reusable as a generic LLM client. |
| Endpoint discovery | static URL composed from `RAY_SERVE_URI` + route prefix | jobs-control-plane lookup, MagicDNS, MLflow tag | URL is fully determined by `(family, suffix, run_name)`; no extra state to keep in sync. |
| Caller -> cluster transport | Serve REST API (`/api/serve/applications/`) | (a) `ray.init` + `serve.run` in caller; (b) submit a Ray job that calls `serve.run` | (a) makes the caller a Ray driver - works on Ray nodes / jobs only, breaks on arc-runners; (b) decouples application lifetime from a job lifetime, then we'd have to babysit the job. REST keeps cortexflow.model_serving HTTP-only and parallels how `cortexflow.remote` talks to Ray Jobs. |
| User-facing shape | abstract `cortexflow.Model` with `infer`/`save`/`load` + generic `/infer` wrapper | user writes their own `@serve.deployment` + FastAPI ingress | Decorator-based version forced the user to know Ray Serve internals and to import `ray.serve` in code that should also run locally. The `Model` ABC keeps user code framework-free; the generic wrapper lives in cortexflow and is the only place that touches `ray.serve`. |
| Bundle timing | bundle the class at `save_model` time, persist URL+import path as MLflow tags | bundle at `deploy_model` time from a passed-in `cls` | Save-time bundling lets `deploy_model` callers be stateless - deploy from any process with just `(family, suffix, run_name)`. Re-deploying old weights under a new class requires re-saving (acceptable: it forces an explicit decision and a fresh registry entry). |
| Code delivery transport | upload to S3, pass via `runtime_env.working_dir` | (a) bake class into cluster image; (b) attach code to the model via MLflow artifacts | (a) cortexflow doesn't own model classes - they live downstream; baking would invert the dependency. (b) MLflow artifact API is slower per-file and not how Ray Serve consumes `working_dir`. |
