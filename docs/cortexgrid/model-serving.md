# Model serving

cortexgrid has two model-related surfaces, and they own strictly different things:

- **Model registry** ([cortexgrid.model_storage](../../cortexgrid/model_storage.py)) - persists a trained model's *weights* to S3 + MLflow Model Registry as an opaque directory, addressable as `(family, suffix, run_name)`. At save time it also bundles the *serve-app* class (its code and every dependency, as source) that will front those weights, so the cluster can deploy it later without the caller holding the class.
- **Model serving** ([cortexgrid.model_serving](../../cortexgrid/model_serving.py)) - schedules the serve-app as a Ray Serve application and returns its URL. cortexgrid imposes no request/response contract; the serve-app owns its own routes, request schemas, streaming, and timeouts.

Both speak the same `(family, suffix, run_name)` triple.

## Responsibility split

cortexgrid owns storage and the Ray Serve deploy plumbing. It does *not* own how a served model handles traffic.

- **cortexgrid's job:** store weights (opaque bytes), store the serve-app code bundle, PUT/GET/DELETE Ray Serve applications over the dashboard REST API, and hand the caller back a URL.
- **the serve-app's job (downstream, e.g. model-gateway):** define the HTTP surface - routes, request/response shapes, token streaming, long-running calls - and reconstruct the model from the weights directory however it likes (HuggingFace `from_pretrained`, `torch.load`, ONNX, a hand-written PyTorch class, ...).

This keeps cortexgrid framework-free: it never imports `transformers` and imposes no `/infer` shape. A completion model can stream tokens on `/complete`; a diffusion model can take minutes on `/generate`; cortexgrid does not need to know.

The weights and the serve-app are *separate objects*. A serve-app is generic - one completion app can front many weight sets - so it is paired with a specific weight set at save time, not baked together.

## Design principle: caller is not the driver

The caller of `save_model`/`deploy_model`/`undeploy_model`/`list_deployed_models` never connects to Ray as a driver. It only speaks HTTP to the Ray dashboard (`RAY_JOB_SERVER_URI`, `/api/serve/applications/`). This mirrors how [cortexgrid.remote](../../cortexgrid/__init__.py) submits jobs: the caller describes the work; turning that into running Ray actors is the cluster's job.

Concretely:

- `submit_ray_job` uses `JobSubmissionClient` -> dashboard `/api/jobs/`.
- `deploy_model` uses the dashboard's `/api/serve/applications/` (PUT to deploy, GET to list). Ray's Serve controller materialises the application.

No `ray.init` anywhere in cortexgrid.model_serving.

## The serve-app

A serve-app is an ordinary class fronted by a FastAPI app, marked with `cortexgrid.serve.ingress`. It declares its resource needs as plain class attributes, takes `(family, suffix, run_name)` in `__init__`, downloads its weights from the registry, and defines whatever routes it wants:

```python
import cortexgrid
from cortexgrid import serve
from fastapi import FastAPI

app = FastAPI()


@serve.ingress(app)
class MyServeApp:
    num_gpus = 1          # class attrs configure the Ray Serve actor
    num_replicas = 1

    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        weights_dir = cortexgrid.load_model(family, suffix, run_name)  # a Path
        self._model = load_however_you_like(weights_dir)

    @app.post("/complete")
    async def complete(self, body: dict):
        ...   # stream, batch, long-running - cortexgrid does not care
```

`serve.ingress` has the same shape as Ray's `ray.serve.ingress`, so the serve-app needs no Ray import. Unlike Ray's, it does not wrap the class: it only records `app` on it and returns the class as written. cortexgrid does not constrain the class either. At deploy time, on the cluster, it applies Ray's `ray.serve.ingress(app)` to a thin subclass of it (see [On the replica](#on-the-replica)) and `serve.deployment(...)` (reading `num_gpus`/`num_replicas`) and binds it with the triple. There is no `cortexgrid.Model` base class and no generic `/infer` route.

Why not `ray.serve.ingress` directly: Ray's decorator replaces the class with a wrapper subclass defined in `ray/serve/api.py`. Older Ray (e.g. 2.9, which the cluster image ran before 2.58) copies only `__name__` onto it, so the wrapper's `__module__` stays `ray.serve.api`. Everything that locates the serve-app by its module - bundling its source at `save_model`, recording its `class_import_path` - then finds Ray's file instead of the serve-app's, and the bundle ships no serve-app code. This bites whenever `save_model` runs where that Ray version is installed, e.g. inside a `cortexgrid.remote` job. Deferring Ray's wrapper to deploy time keeps the class locatable everywhere else, whatever the Ray version. `save_model` rejects a class wrapped by `ray.serve.ingress` with a `ValueError`.

Design note: resource needs are read from plain class attributes rather than a cortexgrid decorator or base class. This is a deliberate, provisional choice (documented in [_serve_entry.py](../../cortexgrid/_serve_entry.py)) - kept minimal until we see how serve-apps declare resources in practice.

## Quick start

```python
import cortexgrid

cortexgrid.Experiment.init("my-experiment")

# 1. Save: upload a weights directory AND bundle the serve-app that fronts it.
#    You produce the weights however you like (training output, HF download);
#    cortexgrid stores the directory opaquely.
saved = cortexgrid.save_model(
    weights_dir,          # a local directory of bytes
    MyServeApp,           # the @serve.ingress class above
    family="qwen",
    suffix="instruct",
)

# 2. Deploy on the cluster. No class object needed; cortexgrid re-imports the
#    serve-app from the import path it captured at save time.
deployed = cortexgrid.deploy_model(
    family="qwen",
    suffix="instruct",
    run_name=cortexgrid.Experiment.get_instance().run_name(),
    wait=True,                        # block until app is RUNNING
)
print(deployed.url)                   # http://<head>:30000/r/qwen/instruct/<run_name>

# 3. Hit it with your own client - the serve-app owns the routes.
import requests
r = requests.post(f"{deployed.url}/complete", json={...}, timeout=600)

# 4. Tear down.
cortexgrid.undeploy_model("qwen", "instruct", "<run_name>")
```

`deploy_model` returns a `Deployment` (URL + identifiers + status), not an inference proxy. Building the client - streaming reader, long timeout, custom request schema - is the caller's job.

No `RAY_ADDRESS`, no Ray Client. The calls go out over HTTP to `RAY_JOB_SERVER_URI` (already in the head secrets store, already used by `cortexgrid.remote`); the cluster handles the rest.

## Code delivery: bundle at save time

`save_model(weights_dir, serve_app, family, suffix)` does two things:

1. **Weights:** uploads `weights_dir` as-is to `s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/` and records that path as the MLflow `ModelVersion.source`. cortexgrid never inspects the contents - the on-disk format is the caller's concern.
2. **Serve-app bundle:** `bundle`s the serve-app class's import graph (same [_bundle.py](../../cortexgrid/_bundle.py) `cortexgrid.remote` uses). Local modules ship as source: the staging dir is zipped under a single top-level `code/` directory and uploaded to `s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip`. The `code/` wrapper is required: Ray unpacks a remote (`s3://`) `working_dir` zip by stripping its top-level directory when there is exactly one, so a bundle of a single package (e.g. only `model_gateway/`) zipped without it would lose that package directory and fail to import on the replica. Jobs are unaffected - the control plane passes a local directory, which Ray zips and unpacks as-is. Third-party distributions are pinned to their installed versions, minus what the worker image already has (`worker_provides()`), and pip-installed on the replica by Ray.

The bundle URL and the serve-app import path are persisted as MLflow tags on the new `ModelVersion`:

| Tag | Meaning |
|---|---|
| `serve_bundle_url` | `s3://...` URL of the zipped staging dir |
| `class_import_path` | `<module>:<ClassName>` of the serve-app to import on the replica |
| `serve_pip_requirements` | JSON list of pinned pip requirements (`["tqdm==4.67.3", ...]`) the replica installs; absent on models saved before this existed, which then install nothing |

`deploy_model` reads those tags back; it does not need the class object, so deployment can happen from any environment that can hit MLflow + the Ray dashboard.

Why bundle at save time (rather than at deploy time)? It keeps `deploy_model` callers stateless - you can deploy from a different process / repo / machine than the one that saved it, with only `(family, suffix, run_name)` in hand. The price is that the serve-app class must be importable in the caller of `save_model`; that is true by construction since `save_model` takes the class object.

Weights are never shipped via `runtime_env` - they stay in S3 and the serve-app's `load_model` call downloads them on `__init__`. The bundler ships *code* only.

### Bundle lifecycle

`delete_model(family, suffix, run_name)` removes the `ModelVersion`, the weights prefix, and the serve bundle. `delete_models_for_run(run_id)` (called by `delete_run` and therefore `delete_experiment`) removes every `ModelVersion` linked to the run, the `models/<run_name>/` prefix, and the `serve-bundles/<run_name>/` prefix. Active Ray Serve deployments are *not* torn down by `delete_run` - call `undeploy_model` explicitly.

## On the replica

`deploy_model` PUTs an application spec whose `import_path` is the generic builder [cortexgrid._serve_entry:build](../../cortexgrid/_serve_entry.py). On the cluster, `build`:

1. Imports the serve-app class from `class_import_path`.
2. If the class was marked by `cortexgrid.serve.ingress`, subclasses it with `_IngressOnReplica` and wraps the subclass with Ray's `ray.serve.ingress(app)`. A class without the mark (a model saved before `cortexgrid.serve` existed, whose class Ray's decorator already wrapped) is used as imported.
3. Reads `num_gpus` / `num_replicas` class attrs.
4. Wraps the class with `serve.deployment(...).options(...)` (`max_ongoing_requests=100`) and binds it with `(family, suffix, run_name)`.

That is the whole of `build` - beyond Ray's own ingress wrapper and `_IngressOnReplica`, it interposes no wrapper and no route.

`_IngressOnReplica` applies `ray.serve.ingress(app)` a second time, in the replica's own process, as Ray creates the instance. Ray's ingress rewrites each route method's signature in place so FastAPI injects the replica instance as `self`, but `build` runs in a different process: the replica imports the serve-app's module afresh, and its route methods carry no rewrite. FastAPI < 0.137 analysed routes once, in `build`'s process, and the replica received the result. FastAPI >= 0.137 analyses them in the replica on the first request, and without the rewrite reads `self` as a required query parameter, so every route answers HTTP 422. The subclass hooks `__new__`, which Ray calls on its own before the serve-app's `__init__`, whether that is sync or async. Checked on Ray 2.9.3 with FastAPI 0.108 and Ray 2.58 with FastAPI 0.141. The serve-app's own `__init__` runs on the replica (calling `cortexgrid.load_model` to download weights), and the serve-app's own routes are what the application exposes under `/r/<family>/<suffix>/<run_name>`.

## API

All exported from `cortexgrid.*`.

### Serve-app

| Function | Purpose |
|----------|---------|
| `serve.ingress(app)` | Class decorator (`from cortexgrid import serve`). Marks the serve-app as fronted by the FastAPI `app` and returns the class unwrapped; Ray's `ray.serve.ingress(app)` is applied on the cluster at deploy time. Use it instead of `ray.serve.ingress`, see [The serve-app](#the-serve-app). |

### Registry

| Function | Purpose |
|----------|---------|
| `save_model(weights_dir, serve_app, family, suffix) -> SavedModel` | Synchronous. Register a new MLflow `ModelVersion` (`uploading`), upload the weights directory + the bundled `serve_app` class/pip deps, flip to `ready`, and return. Uses the current Experiment's `run_id`/`run_name`. See [Model registry lifecycle](#model-registry-lifecycle). |
| `model_registry_status(family, suffix, run_name) -> SavedModel \| None` | The registry lifecycle of one model, or `None` if never registered. `SavedModel.phase` is `uploading` / `ready` / `upload_failed` / `broken`. |
| `load_model(family, suffix, run_name) -> Path` | Download the weights blob to a local directory and return its `Path`. The directory persists after the call; the caller (typically the serve-app) owns its lifetime. cortexgrid does not reconstruct the model. |
| `list_models() -> list[SavedModel]` | Every `ModelVersion` in the registry (including `uploading` / `upload_failed` / `broken`), mapped to a `SavedModel`. |
| `delete_model(family, suffix, run_name)` | Drop the `ModelVersion`, the weights blob, and the serve bundle. Does not undeploy a running Serve app. |

`SavedModel`: `family`, `suffix`, `run_name`, `created_at`, `data_blob_path`, `size_bytes`, `phase`.

### Serving

| Function | Purpose |
|----------|---------|
| `deploy_model(family, suffix, run_name, wait=False, timeout=300.0) -> Deployment` | Read bundle metadata from MLflow, PUT the Serve app spec. Idempotent on `(family, suffix, run_name)`: the app name is deterministic, so a re-PUT replaces. A `DEPLOY_FAILED` app from an earlier attempt is undeployed and, like an app still `deleting`, waited out before the PUT, so the retry starts afresh. With `wait=True`, then blocks as `wait_for_model_serving` does. `timeout` (default 300) caps the whole call. Returns a `Deployment` carrying the app URL. |
| `wait_for_model_serving(family, suffix, run_name, timeout=None)` | Block until the controller reports the app `RUNNING`. Raises `ModelDeployFailed` on `DEPLOY_FAILED` (with the controller's message) and as soon as no app exists for the model; exceeding a finite `timeout` raises `TimeoutError`. `timeout=None` waits unbounded; an app that never leaves `deploying` hangs forever. |
| `undeploy_model(family, suffix, run_name)` | Re-PUT the applications list with this app removed. |
| `model_serving_status(family, suffix, run_name) -> ServingStatus` | The serving lifecycle of one model from the Ray Serve controller; `not_deployed` when no app exists (never raises for a missing app). See [Model serving lifecycle](#model-serving-lifecycle). |
| `list_deployed_models() -> list[Deployment]` | GET `/api/serve/applications/` and return records whose name matches `<family>__<suffix>__<run_name>`, each carrying its serving `phase`. |

`Deployment`: `family`, `suffix`, `run_name`, `url`, `phase`. `ServingStatus`: `family`, `suffix`, `run_name`, `phase`, `message`, `url`. `ModelDeployFailed` subclasses `RuntimeError`. cortexgrid returns these handles and no more; the caller builds whatever HTTP client the serve-app's routes need.

The Ray Serve app name is `<family>__<suffix>__<run_name>`; the route prefix is `/r/<family>/<suffix>/<run_name>`. The serve-app's own routes hang off that prefix (e.g. `{url}/complete`, `{url}/generate`). `family`, `suffix`, `run_name` must not contain `/` or `__`.

## Model registry lifecycle

The registry lifecycle spans a model's life in MLflow + S3: it starts when an upload begins and ends when the model is deleted. Its phase lives on `SavedModel.phase` and is read via `model_registry_status` / `list_models`. It is a separate lifecycle from serving (below).

| phase | meaning |
|---|---|
| `None` | no version registered - no upload has started for this triple |
| `uploading` | the `ModelVersion` exists; weights + serve bundle are streaming to storage |
| `ready` | upload finished; the model is registered and deployable |
| `upload_failed` | `save_model` raised during the upload and marked the version failed |
| `broken` | an upload has stayed `uploading` past the deadline (3h); the writer is presumed dead |

### Uploading a model

`save_model` is **synchronous and blocking**, not async. It:

1. registers a new `ModelVersion` in `uploading` (so the dashboard can surface the in-flight upload immediately),
2. uploads the weights directory and the serve-app bundle to S3 - the slow step; it blocks here,
3. flips the version to `ready` and returns a `SavedModel`.

```python
saved = cortexgrid.save_model(weights_dir, MyServeApp, family="qwen", suffix="instruct")
assert saved.phase == "ready"   # returns only once the upload has landed
```

The call returns only after the upload completes (`ready`) or raises (`upload_failed`). The `uploading` phase is what *other* readers (the dashboard, a concurrent `list_models`) observe while the call is in flight - the caller of `save_model` itself blocks.

Each call creates a *new* `ModelVersion` (MLflow versions are append-only), so saving twice in the same run yields two versions for the same `(family, suffix, run_name)`; save under a fresh run for a clean re-upload.

### Getting upload / model status

```python
status = cortexgrid.model_registry_status("qwen", "instruct", run_name)  # SavedModel | None
if status is None:
    ...   # nothing registered yet
elif status.phase == "uploading":
    ...   # weights still streaming
elif status.phase == "ready":
    ...   # deployable

cortexgrid.list_models()   # every version, including uploading / failed / broken
```

`broken` is derived on read from the version's creation time; it is never written back, so if the weights do eventually land the phase returns to reflecting the real tag.

### Deleting a model

```python
cortexgrid.delete_model("qwen", "instruct", run_name)
```

Removes the `ModelVersion`, the weights prefix, and the serve bundle. It does **not** undeploy a running Serve app - call `undeploy_model` first. `delete_models_for_run(run_id)` (invoked by `delete_run` / `delete_experiment`) removes every version for a run plus its blobs, and likewise leaves Serve apps running.

### Errors and how to fix them

| symptom | cause | fix |
|---|---|---|
| `save_model` raises; version left `upload_failed` | the upload step failed - S3/MinIO unreachable, or a bundling error (the serve-app class, or something it imports, is not importable) | fix the cause (S3 creds, the serve-app's importability), then re-run `save_model`. Remove the dead record with `delete_model`. |
| `save_model` raises `ValueError: ... is wrapped by ray.serve.ingress ...`; version left `upload_failed` | the serve-app is decorated with Ray's `ray.serve.ingress`, whose wrapper hides the serve-app's module on older Ray (see [The serve-app](#the-serve-app)) | decorate it with `cortexgrid.serve.ingress` (`from cortexgrid import serve`), `delete_model` the failed version, and re-run `save_model`. |
| status reads `broken` | the process running `save_model` died mid-upload (kill, OOM, crash), so it never flipped to `ready`/`upload_failed` | `delete_model` the broken version and re-run `save_model`, ideally from a fresh process/run. |
| `deploy_model` raises `ValueError: No saved model for .../cannot deploy` | no registered version for this triple - never saved, wrong triple, or the save is still `uploading` | confirm with `model_registry_status` / `list_models`; save first, or wait for `ready`. |
| `deploy_model` raises `ValueError: ... missing the deployment bundle tag ...` | the version predates bundling or was created outside `save_model` | re-save with `cortexgrid.save_model`. |

## Model serving lifecycle

The serving lifecycle is owned by the Ray Serve controller: it starts at `deploy_model` and ends at `undeploy_model`. Its phase lives on `ServingStatus.phase` (from `model_serving_status`) and `Deployment.phase` (from `list_deployed_models`), normalized from Ray Serve's `ApplicationStatus`.

| phase | meaning |
|---|---|
| `not_deployed` | no Serve app - never deployed, or already undeployed |
| `not_started` | the controller accepted the app but has not started it yet |
| `deploying` | replicas starting; the replica pulls weights and builds the model on the worker |
| `running` | serving traffic |
| `unhealthy` | the app went unhealthy after starting |
| `failed` | deploy failed (`DEPLOY_FAILED`) |
| `deleting` | the app is being torn down |

### Deploying a model

```python
d = cortexgrid.deploy_model("qwen", "instruct", run_name, wait=True, timeout=300)
print(d.url, d.phase)
```

`deploy_model` PUTs the Serve app spec and returns a `Deployment`. With `wait=False` (default) it returns as soon as the controller has accepted the spec; with `wait=True` it then blocks exactly as [`wait_for_model_serving`](#waiting-for-a-model-to-serve) does. `timeout` (default 300) caps the whole call - clearing a [failed app](#re-deploying-a-failed-model), the PUT and the wait; `timeout=None` removes the cap. It is idempotent on the triple - re-deploying replaces the app. The model must be registered and `ready`.

### Waiting for a model to serve

`wait_for_model_serving` blocks until a model's app is `running`. Use it to wait on a deploy started elsewhere - another process, the UI, or an earlier `deploy_model(wait=False)`:

```python
try:
    cortexgrid.wait_for_model_serving("qwen", "instruct", run_name, timeout=1800)
except cortexgrid.ModelDeployFailed as e:
    print(e)   # DEPLOY_FAILED with the controller's message, or no app at all
except TimeoutError:
    ...        # still not running after 1800 s
```

| app's state | what the wait does |
|---|---|
| `running` | returns |
| `not_started`, `deploying`, `unhealthy` | keeps polling, every 2 s |
| `deleting` | keeps polling; once the app is gone, raises `ModelDeployFailed` |
| `failed` | raises `ModelDeployFailed` carrying the controller's message |
| no app | raises `ModelDeployFailed` straight away - the wait never deploys anything itself |
| `timeout` elapses | raises `TimeoutError` with the last status and message |

`ModelDeployFailed` subclasses `RuntimeError`, so code that caught `RuntimeError` from `deploy_model(wait=True)` keeps working. A finite `timeout` always gets at least one status check. With `timeout=None` there is no deadline, so an app stuck `deploying` (e.g. waiting for a free GPU) blocks forever.

An app can disappear mid-wait when `deploy_model` calls overlap: each one GETs the applications list, splices in its own app and PUTs the whole list back, so a later PUT can drop the app an earlier one added. The wait fails fast rather than polling for an app that will not come back.

### Re-deploying a failed model

Calling `deploy_model` for a model whose app is `failed` retries it from scratch:

1. undeploy the failed app;
2. poll until the controller has removed it (an app already `deleting` is waited out the same way);
3. PUT the spec and, with `wait=True`, wait for `running`.

Step 2 is what makes the retry real. Ray resets a failed deployment only when a new deploy arrives after the old deployment was marked for deletion, or when the deployment's version changes. A re-deploy sends an identical spec, so PUTting it over the failed app - or PUTting it back before the controller has processed the undeploy - leaves the failed deployment in place, and the app reports `DEPLOY_FAILED` again without retrying anything.

Apps in any other state are PUT over directly. Re-PUTting an app that is still building restarts the build, so to wait on one, call `wait_for_model_serving` instead of `deploy_model`.

A retry helps with transient failures (OOM on a busy node, a flaky download). A serve-app that fails deterministically - a missing dependency, a bug in `__init__` - fails again: fix it and `save_model` again first, since the bundle is frozen at save time.

### Getting serving status

```python
s = cortexgrid.model_serving_status("qwen", "instruct", run_name)  # ServingStatus
s.phase      # e.g. "deploying" / "running"
s.message    # controller message (populated on failed / unhealthy)
s.url        # route URL, or None when not_deployed

cortexgrid.list_deployed_models()   # every Serve app matching our naming, each a Deployment
```

`model_serving_status` returns `not_deployed` when no app exists; it never raises for a missing app. While `deploying`, the replica is downloading weights and running the serve-app's `__init__` - normal, and can take minutes for large models.

### Undeploying a model

```python
cortexgrid.undeploy_model("qwen", "instruct", run_name)
```

Re-PUTs the applications list without this app; the controller tears down the replicas (transient `deleting`). The registry entry is untouched - the model stays deployable.

### Errors and how to address them

| symptom | cause | fix |
|---|---|---|
| `deploy_model(wait=True)` / `wait_for_model_serving` raises `ModelDeployFailed: Serve app ... DEPLOY_FAILED: <message>` | the replica failed to import or build - a dependency missing from the bundle (something the serve-app imports that was not reachable at `save_model` time), a pinned requirement pip could not install on the replica (a version not on PyPI, no wheel for the worker's platform), an exception in the serve-app `__init__`, or OOM while loading weights | read `<message>`; check the serve-app's imports as bundled at `save_model` time and the replica logs in the Ray dashboard; fix and re-deploy. |
| `deploy_model(wait=True)` / `wait_for_model_serving` raises `TimeoutError: ... did not reach RUNNING within Ns` | app stuck `deploying` - not enough free GPUs for `num_gpus`, a slow image pull, or a hung `__init__` | check GPU availability and the app in the Ray dashboard; free GPUs by undeploying others; raise `timeout` or pass `timeout=None`. |
| `deploy_model(wait=True)` / `wait_for_model_serving` raises `ModelDeployFailed: Serve app ... does not exist` | the app was never deployed, was undeployed, or was dropped by a concurrent `deploy_model` (each deploy PUTs the whole applications list, so a later PUT can drop an app an earlier one added) | check `list_deployed_models`; deploy again. |
| `model_serving_status` reports `failed` | same as `DEPLOY_FAILED`, observed without `wait` | read `ServingStatus.message`; check replica logs; re-deploy after fixing - `deploy_model` clears the failed app itself. |
| `model_serving_status` reports `unhealthy` | the app started, then a replica crashed or health checks began failing | inspect replica logs in the Ray dashboard; re-deploy. |
| deployed but a route returns 404 | wrong route prefix, or hitting the app before `running` | routes hang off `{d.url}` = `/r/<family>/<suffix>/<run_name>`; confirm `phase == "running"` first. |

Observability: each app appears in the Ray dashboard (Serve > Applications) and the Ray Serve Grafana dashboard, both linked from the deployment card in the cortexgrid UI.

## Application spec PUT to Ray

```json
{
  "name": "<family>__<suffix>__<run_name>",
  "route_prefix": "/r/<family>/<suffix>/<run_name>",
  "import_path": "cortexgrid._serve_entry:build",
  "args": {
    "class_import_path": "<module>:<ServeAppClass>",
    "family": "...", "suffix": "...", "run_name": "..."
  },
  "runtime_env": {
    "working_dir": "s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip",
    "pip": ["tqdm==4.67.3", "..."]
  }
}
```

Replica options (`num_replicas`, `max_ongoing_requests`, `ray_actor_options.num_gpus`) are not in the spec - `_serve_entry.build` applies them via `.options(...)` on the serve-app deployment, reading `num_replicas` and `num_gpus` from the class attrs. `max_ongoing_requests` is fixed at 100, the default before Ray 2.32 lowered it to 5.

`deploy_model` reconstructs the full applications list (GET, replace this entry, PUT) because `/api/serve/applications/` is declarative: the PUT body is the desired complete set.

## How it fits together

```
caller (laptop / arc-runner / training job)
  |
  | cortexgrid.save_model(weights_dir, ServeApp, family, suffix)   [synchronous / blocking]
  |   1. MLflow create_model_version(name="<family>__<suffix>", source=..., tags={family, suffix, run_name, size_bytes, lifecycle=uploading})
  |   2. upload weights_dir as-is to s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/
  |   3. bundle_class(ServeApp) -> zip to s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>.zip
  |   4. set tags {serve_bundle_url, class_import_path, serve_pip_requirements}; flip lifecycle=ready
  v
S3 (weights + zipped bundle)
MLflow Model Registry (ModelVersion + tags)

caller
  |
  | cortexgrid.deploy_model(family, suffix, run_name, wait=True)
  |   1. read bundle metadata from MLflow tags
  |   2. app DEPLOY_FAILED? undeploy it; failed or DELETING: poll GET until it is gone
  |   3. PUT /api/serve/applications/  (full applications list)
  |   4. poll GET until the app is RUNNING (DEPLOY_FAILED or a missing app raises ModelDeployFailed)
  v
Ray Serve controller on the cluster
  fetches the bundle zip via runtime_env.working_dir and pip-installs runtime_env.pip into a cached virtualenv
  imports cortexgrid._serve_entry:build, which re-imports the serve-app class
  applies ray.serve.ingress(app) to a subclass of the class marked by cortexgrid.serve.ingress
  each replica re-applies ray.serve.ingress(app) as Ray creates its instance
  binds serve.deployment(ServeApp) -> __init__ calls cortexgrid.load_model(...) -> Path
  exposes the serve-app's own routes under /r/<family>/<suffix>/<run_name>

caller
  |
  | requests.post(f"{deployed.url}/complete", ...)   # caller's own client
  v
HTTP traffic via tailscale, NodePort 30000 on the cluster head
```

## Dependencies

Already deployed in the cluster:
- Ray + Ray Serve ([k8s/workloads/ray/](../../k8s/workloads/ray/), with port 8000 exposed at NodePort 30000)
- MLflow + MinIO ([k8s/workloads/mlflow/](../../k8s/workloads/mlflow/), [k8s/workloads/minio/](../../k8s/workloads/minio/))
- Tailscale ([k8s/workloads/tailscale_operator/](../../k8s/workloads/tailscale_operator/))

cortexgrid secrets used:
- `RAY_JOB_SERVER_URI` - dashboard endpoint; `deploy_model`/`undeploy_model`/`list_deployed_models` PUT/GET against `/api/serve/applications/` here. Same secret `cortexgrid.remote` already uses.
- `RAY_SERVE_URI` - data plane (port 30000), used to compose the deployment URL returned to callers.
- Existing `MLFLOW_TRACKING_URI`, `S3_*` for the registry side and for staging bundles.
- No `RAY_ADDRESS` - the caller does not become a Ray driver.

The serve-app's own runtime dependencies (fastapi, transformers, diffusers, ...) are collected by walking the serve-app's import graph in the environment that ran `save_model`, pinned to the versions installed there, and pip-installed on the replica -- minus whatever the ray worker image already bakes in (`worker_provides()`). Pip fetches wheels for the replica's own platform, so compiled dependencies work across laptop and cluster architectures, provided the pinned version is on PyPI. cortexgrid core does not depend on them.

## Pending work

- **Tear-down policy** for idle deployments. Today only explicit `undeploy_model` releases the GPU; consider an idle eviction policy when the registry has more deployable runs than cluster GPUs.
- **Stale-bundle GC.** Bundles for undeployed-but-not-deleted runs are not currently garbage-collected. If it becomes a problem, the cleanest signal is "no Serve application currently references this bundle URL"; implement at that point, not before.

## Alternatives that were considered

| Topic | Picked | Rejected | Reason |
|---|---|---|---|
| Serving runtime | Ray Serve | vLLM | vLLM is a second serving stack with patchy arm64; revisit when LLM throughput is a measured problem. |
| Weights source | MLflow Model Registry + direct S3 writes | MLflow run artifacts only | Listing speed: registry filtering is O(1) where artifact-walking was O(N) with one GET per manifest. |
| Library shape | cortexgrid + model-gateway separate | merged | Keeps cortexgrid torch-free for laptop callers; model-gateway stays reusable as a generic LLM client. |
| Endpoint discovery | static URL composed from `RAY_SERVE_URI` + route prefix | jobs-control-plane lookup, MagicDNS, MLflow tag | URL is fully determined by `(family, suffix, run_name)`; no extra state to keep in sync. |
| Caller -> cluster transport | Serve REST API (`/api/serve/applications/`) | (a) `ray.init` + `serve.run` in caller; (b) submit a Ray job that calls `serve.run` | (a) makes the caller a Ray driver - works on Ray nodes / jobs only, breaks on arc-runners; (b) decouples application lifetime from a job lifetime, then we'd have to babysit the job. REST keeps cortexgrid.model_serving HTTP-only and parallels how `cortexgrid.remote` talks to Ray Jobs. |
| User-facing shape | user writes their own `@cortexgrid.serve.ingress` serve-app; cortexgrid only stores + deploys it | abstract `cortexgrid.Model` + generic `/infer` wrapper | A generic wrapper forces one request/response contract (unary JSON, fixed timeout) on every model. Real models need token streaming, multi-minute diffusion calls, and custom request schemas - all traffic concerns the serve-app must own. Letting cortexgrid own the wrapper collapsed those; the BYO serve-app keeps cortexgrid framework-free and imposes no HTTP shape. |
| Ingress decorator | `cortexgrid.serve.ingress` records the FastAPI app on the class; `_serve_entry.build` applies `ray.serve.ingress` at deploy time, and `_IngressOnReplica` applies it again in each replica | (a) `ray.serve.ingress` at class definition; (b) locate the serve-app through the wrapper's bases (`__mro__`) in `bundle_class` | (a) Ray's wrapper hides the serve-app's module on older Ray, so bundling ships Ray's file instead of the serve-app (see [The serve-app](#the-serve-app)), and importing `ray.serve` needs the `ray[serve]` extras wherever a serve-app is defined. (b) works, but guesses around a Ray implementation detail and still leaves serve-apps importing Ray. Deferring the wrap needs no guessing, works on every Ray version, and serve-apps import only cortexgrid. Its cost: the wrap runs outside the replica, which FastAPI >= 0.137 needs it in, hence the second application (see [On the replica](#on-the-replica)). |
| Bundle timing | bundle the serve-app class at `save_model` time, persist URL+import path as MLflow tags | bundle at `deploy_model` time from a passed-in `cls` | Save-time bundling lets `deploy_model` callers be stateless - deploy from any process with just `(family, suffix, run_name)`. Re-pairing old weights with a new serve-app requires re-saving (acceptable: it forces an explicit decision and a fresh registry entry). |
| Retrying a failed app | `deploy_model` undeploys a `DEPLOY_FAILED` app and waits until the controller has removed it, then PUTs | (a) PUT the same spec over the failed app; (b) undeploy, then PUT immediately | Ray resets a failed deployment only when a deploy arrives after it was marked for deletion, or when its version changes. (a) keeps the failed deployment: the app reports `DEPLOY_FAILED` again without retrying. (b) races the controller tick that marks the deployments for deletion, with the same result when the PUT wins. |
| Status right after a deploy | read the status as soon as the PUT returns | ignore statuses until the app's `last_deployed_time_s` changes | The PUT is synchronous: the controller registers the app, sets it `DEPLOYING` and stamps `last_deployed_time_s` before responding (checked on Ray 2.9.3 and 2.55), so there is no stale status from a previous attempt to filter out. |
| Code delivery transport | upload to S3, pass via `runtime_env.working_dir` | (a) bake serve-app into cluster image; (b) attach code to the model via MLflow artifacts | (a) cortexgrid doesn't own serve-app classes - they live downstream; baking would invert the dependency. (b) MLflow artifact API is slower per-file and not how Ray Serve consumes `working_dir`. |
