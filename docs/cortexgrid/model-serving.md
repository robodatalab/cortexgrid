# Model serving

cortexgrid has two model-related surfaces, and they own strictly different things:

- **Model registry** ([cortexgrid.model_storage](../../cortexgrid/model_storage.py)) - persists a trained model's *weights* to S3 as an opaque directory, with a registry entry kept by the jobs control plane,, addressable as `(family, suffix, run_name)`. At save time it also bundles the *serve-app* class (its code and every dependency, as source) that will front those weights, so the cluster can deploy it later without the caller holding the class.
- **Model serving** ([cortexgrid.model_serving](../../cortexgrid/model_serving.py)) - schedules the serve-app as a Ray Serve application and returns its URL. cortexgrid imposes no request/response contract; the serve-app owns its own routes, request schemas, streaming, and timeouts.

Both speak the same `(family, suffix, run_name)` triple. A model with no weights of ours - one behind a provider's API - goes through the same two surfaces, registering its serve-app alone; see [Serving a hosted-API model](#serving-a-hosted-api-model).

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

A serve-app is an ordinary class fronted by a FastAPI app, marked with `cortexgrid.serve.ingress`. It takes `(family, suffix, run_name)` in `__init__`, downloads its weights from the registry, and defines whatever routes it wants. It declares no resources: what a replica needs is a property of the model, stored in the registry (see [Model requirements](#model-requirements)), as is anything else about the model the app has to know (see [Model config](#model-config)).

```python
import cortexgrid
from cortexgrid import serve
from fastapi import FastAPI

app = FastAPI()


@serve.ingress(app)
class MyServeApp:
    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        weights_dir = cortexgrid.load_model(family, suffix, run_name)  # a Path
        self._model = load_however_you_like(weights_dir)

    @app.post("/complete")
    async def complete(self, body: dict):
        ...   # stream, batch, long-running - cortexgrid does not care
```

`serve.ingress` has the same shape as Ray's `ray.serve.ingress`, so the serve-app needs no Ray import. Unlike Ray's, it does not wrap the class: it only records `app` on it and returns the class as written. cortexgrid does not constrain the class either. At deploy time, on the cluster, it applies Ray's `ray.serve.ingress(app)` to a thin subclass of it (see [On the replica](#on-the-replica)) and `serve.deployment(...)` (with the resources and replica count `deploy_model` put in the spec) and binds it with the triple. There is no `cortexgrid.Model` base class and no generic `/infer` route.

Why not `ray.serve.ingress` directly: Ray's decorator replaces the class with a wrapper subclass defined in `ray/serve/api.py`. Older Ray (e.g. 2.9, which the cluster image ran before 2.58) copies only `__name__` onto it, so the wrapper's `__module__` stays `ray.serve.api`. Everything that locates the serve-app by its module - bundling its source at `save_model`, recording its `class_import_path` - then finds Ray's file instead of the serve-app's, and the bundle ships no serve-app code. This bites whenever `save_model` runs where that Ray version is installed, e.g. inside a `cortexgrid.remote` job. Deferring Ray's wrapper to deploy time keeps the class locatable everywhere else, whatever the Ray version. `save_model` rejects a class wrapped by `ray.serve.ingress` with a `ValueError`.

Serve-apps written before this carried `num_gpus` / `num_replicas` class attributes. They are ignored now; move the hardware into `ModelRequirements` at save/import time and pass the replica count to `deploy_model`.

## Model requirements

What one replica of a model needs to run - GPUs, RAM, VRAM - belongs to the model, not to the serve-app code that fronts it: the same completion app serves a 0.5B model on a CPU and a 7B one on a GPU. `cortexgrid.ModelRequirements` holds it, `save_model` / `import_model` store it, and `deploy_model` turns it into what Ray needs to place the replica.

```python
requirements = cortexgrid.ModelRequirements(num_gpus=1, ram_gb=8.0, vram_gb=16.0)

cortexgrid.import_model(fetch, MyServeApp, family="qwen", suffix="base", requirements=requirements)

# Corrected later, from code or from the model card in the dashboard:
cortexgrid.set_model_requirements("qwen", "base", cortexgrid.IMPORTED, requirements)
```

RAM and VRAM are in GiB, and all three fields default to 0, which means "no requirement": such a model runs anywhere, a CPU-only node included. `vram_gb` is the GPU memory across the replica's GPUs and needs a GPU share; negative values and VRAM without a GPU raise `ValueError`. Requirements are stored as tags on the registry entry, so `list_models` and the dashboard read them without touching the weights or importing the serve-app class:

| Tag | Meaning |
|---|---|
| `num_gpus` | GPUs one replica gets, whole or a fraction of one |
| `ram_gb` | GiB of RAM reserved for one replica |
| `vram_gb` | GiB of GPU memory across the replica's GPUs |

### Sharing a GPU between models

`num_gpus` is fractional, so a card is not all-or-nothing: `num_gpus=0.25` lets four replicas be served on one GPU. Ray subtracts the share from the node's `GPU` resource like any other, and a model saved before this stored a whole number, which still reads as one.

What makes sharing safe is `vram_gb`, not `num_gpus`. Ray does not isolate co-located replicas - they are handed the same device and can each allocate all of its memory - so the fraction only decides how many may sit on a card, while the `vram_mib` reservation decides whether their combined memory actually fits. Set `vram_gb` honestly on a shared model: two replicas whose real usage exceeds the card will OOM each other, and the one that dies need not be the one that overallocated. Conversely a `num_gpus=1` model has a card to itself no matter how little VRAM it asks for.

A model saved before requirements existed carries none of these tags and reads as no requirement.

### How Ray places a replica

`deploy_model` translates the requirements into the replica's Ray actor resources: `num_gpus` as-is, `ram_gb` as `memory` in bytes, and `vram_gb` as `vram_mib` - a custom Ray resource each GPU worker advertises at startup, being the MiB `nvidia-smi` reports for the GPUs that worker was given (see the [ray-worker DaemonSet](../../k8s/charts/cortexgrid/templates/ray/worker_daemonset.yaml)). Ray then schedules the replica only on a node that has all three free, and reserves them there, so two replicas cannot both claim the same GPU's memory.

MiB, not GiB, because `nvidia-smi` reports MiB and a card's memory is not a whole number of GiB: a "24GB" card has 24564 MiB, and a worker rounding that down to 23 GiB would look too small for a model that fits it.

A GPU with **unified memory** (e.g. the DGX Spark's GB10) has no memory of its own - it shares the host's - and `nvidia-smi` reports `[N/A]` for it. Such a worker advertises the host's `MemTotal` as `vram_mib` instead, since that is what the GPU can use; a node that cannot report a size must still be placeable, or a model that fits it has nowhere to run. On those nodes `ram_gb` and `vram_gb` describe the same pool, so a replica asking for both reserves twice - size requirements for a unified-memory node accordingly.

Two cluster-side limits shape what can actually be asked for:

- Ray sizes a worker's `memory` from the pod's cgroup limit, so the worker pods carry no memory limit and Ray sees the host's own RAM. Ray keeps roughly 30% of it for its object store; the rest is what replicas can reserve.
- A GPU worker gets one GPU, so a model needing `num_gpus > 1` has no node to land on today. A fraction of one is fine - see [Sharing a GPU between models](#sharing-a-gpu-between-models).

A requirement no node can satisfy is not an error: the app stays `deploying` until one frees up, or until `deploy_model(wait=True)` times out.

### Which GPU it picks, when several fit

Resources decide *whether* a node can host a replica; they say nothing about *which* of the nodes that can should. Ray's default policy is hybrid packing - it fills the first feasible node to about half its capacity, then spreads - so a 4 GiB model was as likely to land on a 128 GiB card as on a 12 GiB one, and the 128 GiB card was then unavailable for a model that genuinely needed it.

So `deploy_model` asks for the smallest card that fits, and only moves up when the smaller ones are full. Each GPU worker sets the MiB it advertises as `vram_mib` as a **node label** of the same name, next to the resource. The two are deliberately different things:

| | what it is | what it says |
|---|---|---|
| `vram_mib` resource | consumable, shrinks as replicas take it | how much of the card is still free |
| `vram_mib` label | static, fixed at worker startup | how big the card is |

`vram_tiers()` reduces the labels of the ALIVE nodes to the cluster's distinct GPU **size classes**, smallest first - one entry per size, not per node. `deploy_model` then hands the replica the smallest class the model fits in as `label_selector`, and the larger ones, in order, as `fallback_strategy`. For a 4 GiB model on a cluster of 12 GiB and 128 GiB cards:

```python
{"label_selector": {"vram_mib": "12282"},
 "fallback_strategy": [{"label_selector": {"vram_mib": "131072"}}]}
```

A fallback fires on **exhaustion**, not merely on a size class being absent, which is what makes this "the smallest card that is free" rather than "the smallest card that exists". Verified on Ray 2.58 against a two-node cluster: of three 4 GiB replicas, two filled the 12282 MiB card (a third would need 12288 MiB) and the next spilled to the 128 GiB one. This is why `pyproject.toml` floors Ray at 2.58 - Ray Serve only accepts `label_selector` / `fallback_strategy` in a replica's `ray_actor_options` from 2.55 on.

Three things follow from these being *preferences* over an unchanged reservation:

- **Nothing is pinned to a node.** The selectors name a size class, not a machine, so a replica that has to restart is placed against whatever is alive then.
- **The chain never goes stale silently.** Its last link is a catch-all excluding the sizes known to be *too small* (`!in(12282)`) rather than naming the ones that fit, so a larger card joining the cluster after a deploy is still placeable. It is dropped when no known size is too small, having nothing left to say. A model that no current card fits gets only the catch-all, so it waits for a big enough card instead of being offered one that cannot work.
- **A model with no `vram_gb` gets no selector at all**, leaving it placeable on a CPU-only node - which carries no `vram_mib` label. The same is true when the cluster reports no GPU sizes, so a label-less cluster places exactly as it did before.

The tiers are re-read on every `deploy_model` that reaches Ray, so adding or removing a GPU changes the spec and re-PUTs it rather than being remembered from an earlier deploy. A redeploy that changes nothing returns from the model's deployment record without asking Ray at all; it checks the spec against the tiers the record was deployed with, so a tier change reaches the spec on the next deploy that changes anything else (larger new GPUs stay usable meanwhile through the catch-all fallback).

### Seeing where a replica actually landed

`model_replica_placements(family, suffix, run_name)` reports one `ReplicaPlacement` per live replica - its id, its state, and the node Ray put it on. Ray carries this under each deployment's `replicas` in the Serve details; the jobs control plane copies it into the model's deployment record on every poll cycle, and this reads it from there.

`node_ip` is the address of the Ray worker, which on the cluster is the ray-worker **pod's** IP - the DaemonSet does not use `hostNetwork`. That address alone does not name a machine, so the dashboard's `/api/deployments/{family}/{suffix}/{run_name}/devices` joins it to kubernetes with `infra_status.device_for_ip`, which looks the pod up by `status.podIP` and returns it with the same health verdict the Infrastructure Status tab computes. The deployment card renders it with the same `DeviceCard` slate that tab uses, so there is one rendering of a machine's health rather than two that can disagree.

A replica Ray has not placed yet has no `node_ip`, and a worker that has since gone has one kubernetes no longer knows; both still appear on the card, without a device.

### Migrating a serve-app written before this

A serve-app that declared `num_gpus` / `num_replicas` as class attributes (the shape cortexgrid documented until now) needs three changes, all in the repo that owns it:

1. Delete both attributes from the class.
2. Pass `requirements=ModelRequirements(...)` to every `save_model` / `import_model` call for the models that class fronts.
3. Pass `num_replicas` to `deploy_model` where more than one replica was wanted.

Do them together. The attributes keep working only while the model's bundle predates this change; the first `import_model` after upgrading cortexgrid re-bundles the serve-app (the bundled `_serve_entry.py` changed, so its fingerprint did), and from that deploy on the attributes are ignored. A model whose requirements are still unset then asks for no GPU and is placed on whatever node is free - including a CPU-only one. A model already in the registry keeps its stored requirements, so step 2 fills them in for models that have none and leaves corrected ones alone.

### Changing them later

`set_model_requirements(family, suffix, run_name, requirements)` replaces the stored values; the dashboard's model card edits them the same way. It takes effect on the next `deploy_model` - a replica already running keeps the placement it started with. `import_model` stores requirements only when it uploads the model or finds none stored, so an edit made since is not overwritten by the next run that imports it.

## Model config

Some models need more than weights and hardware: which model a provider should be asked for, an endpoint, the name of the secret holding a key. That is not the serve-app's code - the same app fronts every model of its kind - and not the weights, which a hosted model does not have. So it lives on the registry entry: a free-form `dict[str, str]` cortexgrid stores and never interprets.

```python
cortexgrid.register_model(              # a hosted model brings no weights
    AnthropicServeApp,
    family="anthropic",
    suffix="opus",
    config={"model": "claude-opus-5", "api_key_secret": "anthropic-api-key"},
)

# Corrected later, from code or from the model card in the dashboard:
cortexgrid.set_model_config(
    "anthropic", "opus", cortexgrid.IMPORTED, {"model": "claude-sonnet-5"}
)
```

The serve-app reads it at construction, from the identifiers it was constructed with - no extra argument, so an app written before this keeps working:

```python
@serve.ingress(app)
class AnthropicServeApp:
    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        config = cortexgrid.model_config(family, suffix, run_name)
        self._model = config["model"]
        self._key = cortexgrid.get_secret(config["api_key_secret"])
```

Keys and values are strings: they round-trip through a tag and the model card edits them as text, so a number or a flag is spelled as a string and the serve-app parses it back. Anything else raises `ValueError`, as does a blank key.

The whole mapping is stored as one JSON object in the registry entry's tag `config`, so `list_models` and the dashboard read it without touching the weights or importing the serve-app class. One tag rather than one per key: the keys are the serve-app's to choose, and removing one needs no tag deletion. A model stored without a config carries no tag and reads as `{}`.

A tag is readable by anyone with registry access, so a credential itself does not belong here: put it in `cortexgrid.set_secret` and let the config carry its name, as `api_key_secret` does above. See [Serving a hosted-API model](#serving-a-hosted-api-model) for that whole sequence - serve-app, key, registration, deploy - in one piece.

### Changing it later

`set_model_config(family, suffix, run_name, config)` replaces the whole mapping - a key left out of `config` is gone - and the dashboard's model card edits it the same way. The serve-app reads the config at construction, so a change takes effect on the model's next `deploy_model`; a replica already running keeps the values it started with. `import_model` stores a config only when it uploads the model or finds none stored, so an edit made since is not overwritten by the next run that imports it.

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
    # What one replica needs to run. Placement is matched against it.
    requirements=cortexgrid.ModelRequirements(num_gpus=1, ram_gb=8, vram_gb=16),
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

## Serving a hosted-API model

A model behind a provider's API - Gemini, OpenAI, Anthropic - is served exactly like one with weights, minus the weights. The serve-app forwards requests instead of running a model, the API key lives in the secrets store, and which model to ask for travels on the registry entry as [config](#model-config). End to end, for Gemini:

```python
import cortexgrid
import requests
from cortexgrid import serve
from fastapi import FastAPI
from google import genai

app = FastAPI()


# 1. The serve-app. Nothing to load: it reads its settings from the registry
#    entry it was constructed with, and the key from the secrets store. Its
#    routes are its own - cortexgrid imposes no request/response shape.
@serve.ingress(app)
class GeminiServeApp:
    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        config = cortexgrid.model_config(family, suffix, run_name)
        self._model = config["model"]
        self._client = genai.Client(
            api_key=cortexgrid.get_secret(config["api_key_secret"])
        )

    @app.post("/complete")
    async def complete(self, body: dict) -> dict:
        response = await self._client.aio.models.generate_content(
            model=self._model, contents=body["prompt"]
        )
        return {"text": response.text}


cortexgrid.Experiment.init("my-experiment")

# 2. The key, stored once for the cluster. Never in the config: a registry tag
#    is readable by anyone who can see the model, a secret is not.
cortexgrid.set_secret("gemini-api-key", "AIza...")

# 3. Register. There are no weights to upload - only GeminiServeApp's code is
#    bundled and stored.
m = cortexgrid.register_model(
    GeminiServeApp,
    family="gemini",
    suffix="flash",
    config={"model": "gemini-2.5-flash", "api_key_secret": "gemini-api-key"},
)

# 4. Deploy and call it like any other model. m.run_name is cortexgrid.IMPORTED.
deployed = cortexgrid.deploy_model(m.family, m.suffix, m.run_name, wait=True)
requests.post(f"{deployed.url}/complete", json={"prompt": "hello"}, timeout=60)
```

Run that again and nothing is re-registered: the entry is written once under `(gemini, flash, cortexgrid.IMPORTED)` and later calls only re-bundle `GeminiServeApp` if its code changed, so the snippet can sit at the top of a script or a job. See [Registering a model with no weights](#registering-a-model-with-no-weights) for what happens when an attempt is already in flight or failed.

Four things follow from there being no weights:

- **No hardware is asked for.** A replica that only forwards HTTP needs no GPU, so `requirements` is left out and Ray places it on any node, CPU-only included. Pass `ModelRequirements(...)` only if the app itself needs something.
- **`load_model` raises** on this model, and `SavedModel.has_weights` is `False`. The serve-app must not call it.
- **The rest of the registry does not care.** `deploy_model`, `model_registry_status`, `list_models`, `undeploy_model`, `delete_model` and the dashboard treat the entry like any other, and the URL has the usual shape (`.../r/gemini/flash/imported`), so a client cannot tell a hosted model from a local one.
- **Settings change without a re-register.** `cortexgrid.set_model_config("gemini", "flash", cortexgrid.IMPORTED, {...})` - or the model card in the dashboard - points the entry at another provider model or another secret; the serve-app reads config at construction, so it takes effect on the next `deploy_model`.

Another provider is another serve-app class and another entry: same `register_model` call, same key shape, a different `config`. Rotating a key is `set_secret` under the same name, and takes effect when the replica is next constructed.

## Code delivery: bundle at save time

`save_model(weights_dir, serve_app, family, suffix)` does two things:

1. **Weights:** uploads `weights_dir` as-is to `s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/` and records that path as the registry entry's `source`. cortexgrid never inspects the contents - the on-disk format is the caller's concern.
2. **Serve-app bundle:** `bundle`s the serve-app class's import graph (same [_bundle.py](../../cortexgrid/_bundle.py) `cortexgrid.remote` uses). Local modules ship as source: the staging dir is zipped under a single top-level `code/` directory and uploaded to `s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>/<fingerprint>.zip`. The fingerprint hashes everything the replica runs - the bundled files, the serve-app import path, and the pip requirements - and is part of the URL because Ray keeps a remote `working_dir` it has downloaded and reuses it for the same URL, so new code at an old URL would never reach a replica. The `code/` wrapper is required: Ray unpacks a remote (`s3://`) `working_dir` zip by stripping its top-level directory when there is exactly one, so a bundle of a single package (e.g. only `model_gateway/`) zipped without it would lose that package directory and fail to import on the replica. Jobs are unaffected - the control plane passes a local directory, which Ray zips and unpacks as-is. Third-party distributions are pinned to their installed versions, minus what the worker image already has (`worker_provides()`), and pip-installed on the replica by Ray.

The bundle URL and the serve-app import path are persisted as tags on the new registry entry:

| Tag | Meaning |
|---|---|
| `serve_bundle_url` | `s3://...` URL of the zipped staging dir |
| `class_import_path` | `<module>:<ClassName>` of the serve-app to import on the replica |
| `serve_pip_requirements` | JSON list of pinned pip requirements (`["tqdm==4.67.3", ...]`) the replica installs; absent on models saved before this existed, which then install nothing |
| `serve_bundle_fingerprint` | fingerprint of the bundle at `serve_bundle_url`; `import_model` compares it to re-bundle changed serve-app code. Absent on models saved before this existed |

`deploy_model` reads those tags back; it does not need the class object, so deployment can happen from any environment that can reach the jobs control plane + the Ray dashboard.

Why bundle at save time (rather than at deploy time)? It keeps `deploy_model` callers stateless - you can deploy from a different process / repo / machine than the one that saved it, with only `(family, suffix, run_name)` in hand. The price is that the serve-app class must be importable in the caller of `save_model`; that is true by construction since `save_model` takes the class object.

Weights are never shipped via `runtime_env` - they stay in S3 and the serve-app's `load_model` call downloads them on `__init__`. The bundler ships *code* only.

### Bundle lifecycle

`delete_model(family, suffix, run_name)` removes the registry entry, the weights prefix, and the serve bundle. `delete_models_for_run(run_id)` (called by `delete_run` and therefore `delete_experiment`) removes every registry entry linked to the run, the `models/<run_name>/` prefix, and the `serve-bundles/<run_name>/` prefix; imported models are linked to no run and are left in place. Active Ray Serve deployments are *not* torn down by `delete_run` - call `undeploy_model` explicitly.

## On the replica

`deploy_model` PUTs an application spec whose `import_path` is the generic builder [cortexgrid._serve_entry:build](../../cortexgrid/_serve_entry.py). On the cluster, `build`:

1. Imports the serve-app class from `class_import_path`.
2. If the class was marked by `cortexgrid.serve.ingress`, subclasses it with `_IngressOnReplica` and wraps the subclass with Ray's `ray.serve.ingress(app)`. A class without the mark (a model saved before `cortexgrid.serve` existed, whose class Ray's decorator already wrapped) is used as imported.
3. Wraps the class with `serve.deployment(...).options(...)` - the replica count and Ray resources from the spec's `args`, `max_ongoing_requests=100` - and binds it with `(family, suffix, run_name)`.

That is the whole of `build` - beyond Ray's own ingress wrapper and `_IngressOnReplica`, it interposes no wrapper and no route.

`build` ships inside the model's bundle, frozen at save time, while the `args` come from whichever cortexgrid deploys it, so the two versions can differ. A `build` older than the requirements reads neither key and falls back to the serve-app's class attributes, which is how models saved before this change keep deploying as they did. A `build` newer than the deployer falls back to one replica and no resource requests.

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
| `save_model(weights_dir, serve_app, family, suffix, requirements=None, config=None) -> SavedModel` | Synchronous. Register the model (`uploading`), upload the weights directory + the bundled `serve_app` class/pip deps, flip to `ready`, and return. Uses the current Experiment's `run_id`/`run_name`, so every run saves a new copy - meant for weights the run produced. See [Model registry lifecycle](#model-registry-lifecycle). |
| `import_model(source, serve_app, family, suffix, requirements=None, config=None) -> SavedModel` | Register a model produced elsewhere under `(family, suffix, IMPORTED)`, once; returns the existing model when it is already `ready`, re-bundling `serve_app` if its code changed. `source` is the weights directory or a callable returning it. Stores `requirements` and `config` only on the upload, or when the model has none. Tags the current Experiment's run with the model it used. See [Importing a model](#importing-a-model). |
| `register_model(serve_app, family, suffix, requirements=None, config=None) -> SavedModel` | `import_model` for a model that stages no weights - a serve-app forwarding to a hosted API has nothing to upload but its bundle. Same key, same once-only registration and re-bundling, same run tag; `data_blob_path` reads `NO_WEIGHTS`, `has_weights` is `False`. See [Registering a model with no weights](#registering-a-model-with-no-weights). |
| `set_model_requirements(family, suffix, run_name, requirements)` | Replace the hardware one replica of the model needs. Takes effect on its next `deploy_model`. Raises `ValueError` if the model was never registered. See [Model requirements](#model-requirements). |
| `model_config(family, suffix, run_name) -> dict[str, str]` | The config stored on the model, `{}` if it has none. Meant for the serve-app to call in `__init__`. Raises `ValueError` if the model was never registered. See [Model config](#model-config). |
| `set_model_config(family, suffix, run_name, config)` | Replace the model's config - the whole mapping, so a key left out is removed. Takes effect on its next `deploy_model`. Raises `ValueError` if the model was never registered, or if the mapping is not strings. See [Model config](#model-config). |
| `model_registry_status(family, suffix, run_name) -> SavedModel \| None` | The registry lifecycle of one model, or `None` if never registered. `SavedModel.phase` is `uploading` / `ready` / `upload_failed` / `broken`. |
| `load_model(family, suffix, run_name) -> Path` | Download the weights blob to a local directory and return its `Path`. The directory persists after the call; the caller (typically the serve-app) owns its lifetime. cortexgrid does not reconstruct the model. Raises `ValueError` for a model registered with `register_model`, which has no weights to hand back. |
| `list_models() -> list[SavedModel]` | Every entry in the registry (including `uploading` / `upload_failed` / `broken`), mapped to a `SavedModel`. |
| `delete_model(family, suffix, run_name)` | Drop the registry entry, the weights blob, and the serve bundle. Does not undeploy a running Serve app. |

`SavedModel`: `family`, `suffix`, `run_name`, `created_at`, `data_blob_path`, `size_bytes`, `phase`, `requirements`, `config`, `has_weights`. `ModelRequirements`: `num_gpus`, `ram_gb`, `vram_gb`.

### Serving

| Function | Purpose |
|----------|---------|
| `deploy_model(family, suffix, run_name, num_replicas=1, wait=False, timeout=300.0) -> Deployment` | Read the bundle metadata and requirements from the registry, PUT the Serve app spec - each replica requesting the model's requirements. Idempotent on `(family, suffix, run_name)`: the app name is deterministic, so a re-PUT replaces. A `DEPLOY_FAILED` app from an earlier attempt is undeployed and, like an app still `deleting`, waited out before the PUT, so the retry starts afresh. With `wait=True`, then blocks as `wait_for_model_serving` does. `timeout` (default 300) caps the whole call. Records the deployment with the jobs control plane; a redeploy its record shows live with the same spec returns without asking Ray. Returns a `Deployment` carrying the app URL. |
| `wait_for_model_serving(family, suffix, run_name, timeout=None)` | Block until the controller reports the app `RUNNING`. Raises `ModelDeployFailed` on `DEPLOY_FAILED` (with the controller's message) and as soon as no app exists for the model; exceeding a finite `timeout` raises `TimeoutError`. `timeout=None` waits unbounded; an app that never leaves `deploying` hangs forever. |
| `undeploy_model(family, suffix, run_name)` | Re-PUT the applications list with this app removed. |
| `model_serving_status(family, suffix, run_name) -> ServingStatus` | The serving lifecycle of one model, as the jobs control plane last observed it on the Ray Serve controller (at most one poll cycle old); `not_deployed` when no app exists (never raises for a missing app). See [Model serving lifecycle](#model-serving-lifecycle). |
| `list_deployed_models() -> list[Deployment]` | Every model `deploy_model` put on Ray Serve whose app the control plane last saw existing, each carrying its serving `phase`. |
| `model_replica_placements(family, suffix, run_name) -> list[ReplicaPlacement]` | Which worker each live replica landed on - what the requirements and the size-class preferences resolved to. Empty when no app exists; fills in as replicas are placed. See [Seeing where a replica actually landed](#seeing-where-a-replica-actually-landed). |

`Deployment`: `family`, `suffix`, `run_name`, `url`, `phase`. `ServingStatus`: `family`, `suffix`, `run_name`, `phase`, `message`, `url`. `ReplicaPlacement`: `replica_id`, `state`, `node_id`, `node_ip`. `ModelDeployFailed` subclasses `RuntimeError`. cortexgrid returns these handles and no more; the caller builds whatever HTTP client the serve-app's routes need.

The Ray Serve app name is `<family>__<suffix>__<run_name>`; the route prefix is `/r/<family>/<suffix>/<run_name>`. The serve-app's own routes hang off that prefix (e.g. `{url}/complete`, `{url}/generate`). `family`, `suffix`, `run_name` must not contain `/` or `__`.

## Model registry lifecycle

The registry lifecycle spans a model's life in the registry + S3: it starts when an upload begins and ends when the model is deleted. Its phase lives on `SavedModel.phase` and is read via `model_registry_status` / `list_models`. It is a separate lifecycle from serving (below).

| phase | meaning |
|---|---|
| `None` | no version registered - no upload has started for this triple |
| `uploading` | the registry entry exists; `import_model`'s `source` is fetching the weights, or weights + serve bundle are streaming to storage |
| `ready` | upload finished; the model is registered and deployable |
| `upload_failed` | `save_model` / `import_model` raised during the upload and marked the version failed |
| `broken` | an upload has stayed `uploading` past the deadline (3h); the writer is presumed dead |

### Uploading a model

`save_model` is **synchronous and blocking**, not async. It:

1. registers the entry in `uploading` (so the dashboard can surface the in-flight upload immediately),
2. uploads the weights directory and the serve-app bundle to S3 - the slow step; it blocks here,
3. flips the version to `ready` and returns a `SavedModel`.

```python
saved = cortexgrid.save_model(weights_dir, MyServeApp, family="qwen", suffix="instruct")
assert saved.phase == "ready"   # returns only once the upload has landed
```

The call returns only after the upload completes (`ready`) or raises (`upload_failed`). The `uploading` phase is what *other* readers (the dashboard, a concurrent `list_models`) observe while the call is in flight - the caller of `save_model` itself blocks.

There is one entry per `(family, suffix, run_name)`: saving twice in the same run replaces the first save's entry, and the weights land at the same path.

### Importing a model

`save_model` keys a model by the run that saved it, which suits fine-tuned output: every run of the training code produces a new model. A model produced elsewhere - e.g. a pretrained base model pulled from HuggingFace - should be uploaded once and reused by every run. `import_model` registers it under the fixed run_name `cortexgrid.IMPORTED` (`"imported"`; haikunator run names are `word-word-NN`, so no run can take it):

```python
def fetch() -> Path:   # runs only when the model is not registered yet
    return Path(huggingface_hub.snapshot_download("Qwen/Qwen2.5-0.5B-Instruct"))

m = cortexgrid.import_model(fetch, MyServeApp, family="qwen", suffix="base")
cortexgrid.deploy_model(m.family, m.suffix, m.run_name, wait=True)   # m.run_name == cortexgrid.IMPORTED
```

If a version is already registered under `(family, suffix, IMPORTED)`:

| phase | `import_model` |
|---|---|
| `ready` | returns it without calling `source`; re-bundles `serve_app` first if its code changed (see below) |
| `uploading` | raises `RuntimeError` - another process is importing it |
| `upload_failed` / `broken` | deletes it and imports again |

`source` is called only after the new version is registered in `uploading`, so a second `import_model` of the same model raises instead of starting a download of its own while the first one is still fetching. If `source` raises, the version is marked `upload_failed`.

An imported model belongs to no run, so the run records the link instead: every successful `import_model` - whether it uploaded the model or reused it - tags the current Experiment's run with `imported_model/<family>/<suffix>` set to the version's `created_at`. Like `save_model`, it needs an active Experiment.

Weights are imported once, but the serve-app code can change with the library that provides it. On a `ready` model, `import_model` builds the bundle locally and compares its fingerprint with the version's `serve_bundle_fingerprint`; when they differ (or the tag is absent), it uploads the new bundle next to the old one and points the version's bundle tags at it, leaving the weights untouched. The next `deploy_model` runs the new code; an app that is already running keeps the code it started with until it is deployed again, and the old bundle stays in storage so that app can still restart. Every call pays for the local build (walking the serve-app's imports and hashing its files). Callers with different installed dependency versions produce different fingerprints, so each re-bundles when it imports.

To replace an imported model's weights, `undeploy_model` and `delete_model(family, suffix, cortexgrid.IMPORTED)`, then import again. The weights and bundle land under the same layout as a saved model (`models/imported/...`, `serve-bundles/imported/...`), so `load_model`, `deploy_model` and the rest work unchanged. The entry is linked to no run, so `delete_run` / `delete_experiment` leave it in place.

### Registering a model with no weights

Not every model is bytes we hold. A serve-app that forwards to a hosted API stages nothing, and neither does one that reaches for its weights itself at startup. `register_model` files such a model under the same fixed key as an import, with the same once-only semantics, and uploads its bundle and nothing else ([Serving a hosted-API model](#serving-a-hosted-api-model) walks one through end to end):

```python
m = cortexgrid.register_model(
    AnthropicServeApp,
    family="anthropic",
    suffix="opus",
    config={"model": "claude-opus-5", "api_key_secret": "anthropic-api-key"},
)
cortexgrid.deploy_model(m.family, m.suffix, m.run_name, wait=True)
```

What the deployment needs in place of weights goes in `config`, which the serve-app reads at construction - see [Model config](#model-config). A replica that holds no weights and does no compute of its own asks for nothing and is placed on any node, CPU-only included; pass `requirements` if that is not true.

The entry is an ordinary one: `deploy_model`, `model_registry_status`, `list_models`, the dashboard and `delete_model` treat it like any other model, a version already registered under the key is reused, re-bundled or replaced exactly as [Importing a model](#importing-a-model) describes, and the calling run is tagged `imported_model/<family>/<suffix>` the same way.

The difference is the weights that are not there. The entry's source reads `cortexgrid.NO_WEIGHTS` (`"cortexgrid://no-weights"`) rather than an S3 path - the absence is spelled out instead of left blank, so the dashboard's storage field says why there is no path. `size_bytes` is 0, `SavedModel.has_weights` is `False`, and `load_model` raises `ValueError`: there is nothing to hand back.

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

Removes the registry entry, the weights prefix, and the serve bundle. It does **not** undeploy a running Serve app - call `undeploy_model` first. `delete_models_for_run(run_id)` (invoked by `delete_run` / `delete_experiment`) removes every entry for a run plus its blobs, and likewise leaves Serve apps running.

### Errors and how to fix them

| symptom | cause | fix |
|---|---|---|
| `save_model` raises; version left `upload_failed` | the upload step failed - S3/MinIO unreachable, or a bundling error (the serve-app class, or something it imports, is not importable) | fix the cause (S3 creds, the serve-app's importability), then re-run `save_model`. Remove the dead record with `delete_model`. |
| `save_model` raises `ValueError: ... is wrapped by ray.serve.ingress ...`; version left `upload_failed` | the serve-app is decorated with Ray's `ray.serve.ingress`, whose wrapper hides the serve-app's module on older Ray (see [The serve-app](#the-serve-app)) | decorate it with `cortexgrid.serve.ingress` (`from cortexgrid import serve`), `delete_model` the failed version, and re-run `save_model`. |
| status reads `broken` | the process running `save_model` died mid-upload (kill, OOM, crash), so it never flipped to `ready`/`upload_failed` | `delete_model` the broken version and re-run `save_model`, ideally from a fresh process/run. |
| `deploy_model` raises `ValueError: No saved model for .../cannot deploy` | no registered version for this triple - never saved, wrong triple, or the save is still `uploading` | confirm with `model_registry_status` / `list_models`; save first, or wait for `ready`. |
| `deploy_model` raises `ValueError: ... missing the deployment bundle tag ...` | the version predates bundling or was created outside `save_model` | re-save with `cortexgrid.save_model`. |

## Model serving lifecycle

The serving lifecycle is owned by the Ray Serve controller: it starts at `deploy_model` and ends at `undeploy_model`. The jobs control plane mirrors it into each model's deployment record on every poll cycle, and its phase lives on `ServingStatus.phase` (from `model_serving_status`) and `Deployment.phase` (from `list_deployed_models`), normalized from Ray Serve's `ApplicationStatus`.

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

cortexgrid.list_deployed_models()   # every model deploy_model put on Serve, each a Deployment
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
| `deploy_model(wait=True)` / `wait_for_model_serving` raises `TimeoutError: ... did not reach RUNNING within Ns` | app stuck `deploying` - no node has the model's [requirements](#model-requirements) free (GPUs, RAM, VRAM), or none can ever satisfy them; a slow image pull; a hung `__init__` | compare the requirements with the cluster's free resources in the Ray dashboard; free some by undeploying others, or correct the requirements on the model card; raise `timeout` or pass `timeout=None`. |
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
    "family": "...", "suffix": "...", "run_name": "...",
    "num_replicas": 1,
    "ray_actor_options": {
      "num_gpus": 1,
      "memory": 8589934592,
      "resources": {"vram_mib": 16384},
      "label_selector": {"vram_mib": "24564"},
      "fallback_strategy": [
        {"label_selector": {"vram_mib": "131072"}},
        {"label_selector": {"vram_mib": "!in(12282)"}}
      ]
    }
  },
  "runtime_env": {
    "working_dir": "s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>/<fingerprint>.zip",
    "pip": ["tqdm==4.67.3", "..."]
  }
}
```

Replica options travel in `args`: `deploy_model` derives `ray_actor_options` from the model's [requirements](#model-requirements) (`memory` in bytes, `vram_mib` in MiB) and `_serve_entry.build` applies them, with `num_replicas`, via `.options(...)` on the serve-app deployment. `memory` and `resources` are left out when the requirement is 0, so nothing is reserved. `max_ongoing_requests` is fixed at 100, the default before Ray 2.32 lowered it to 5.

`label_selector` and `fallback_strategy` are the size-class preferences from [Which GPU it picks](#which-gpu-it-picks-when-several-fit), built from the cluster's GPU tiers at deploy time - above, a 16 GiB model on a cluster of 12282 / 24564 / 131072 MiB cards. They are absent for a model with no `vram_gb`, and for a cluster reporting no tiers. Because they are part of the spec, a GPU joining or leaving changes it, which is what makes the next `deploy_model` re-PUT rather than skip as already-deployed.

`deploy_model` reconstructs the full applications list (GET, replace this entry, PUT) because `/api/serve/applications/` is declarative: the PUT body is the desired complete set.

## How it fits together

```
caller (laptop / arc-runner / training job)
  |
  | cortexgrid.save_model(weights_dir, ServeApp, family, suffix)   [synchronous / blocking]
  |   1. PUT models/<family>/<suffix>/<run_name> (source=..., tags={family, suffix, run_name, lifecycle=uploading, num_gpus, ram_gb, vram_gb, config})
  |   2. set tag size_bytes; upload weights_dir as-is to s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/
  |   3. bundle_class(ServeApp) -> zip to s3://<bucket>/serve-bundles/<run_name>/<family>__<suffix>/<fingerprint>.zip
  |   4. set tags {serve_bundle_url, class_import_path, serve_pip_requirements, serve_bundle_fingerprint}; flip lifecycle=ready
  v
S3 (weights + zipped bundle)
jobs control plane: registry entry (source + tags) in Postgres

caller
  |
  | cortexgrid.deploy_model(family, suffix, run_name, wait=True)
  |   1. read bundle metadata + requirements from the registry entry's tags; its deployment record live with this spec? return it
  |   2. app DEPLOY_FAILED? undeploy it; failed or DELETING: poll GET until it is gone
  |   3. PUT /api/serve/applications/  (full applications list)
  |   4. poll GET until the app is RUNNING (DEPLOY_FAILED or a missing app raises ModelDeployFailed)
  v
Ray Serve controller on the cluster
  places each replica on a node with the requested GPUs / memory / vram_mib free
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
- The jobs control plane (registry and deployment records) + MinIO ([k8s/workloads/minio/](../../k8s/workloads/minio/))
- Tailscale ([k8s/workloads/tailscale_operator/](../../k8s/workloads/tailscale_operator/))

cortexgrid secrets used:
- `RAY_JOB_SERVER_URI` - dashboard endpoint; `deploy_model`/`undeploy_model`/`list_deployed_models` PUT/GET against `/api/serve/applications/` here. Same secret `cortexgrid.remote` already uses.
- `RAY_SERVE_URI` - data plane (port 30000), used to compose the deployment URL returned to callers.
- `JOBS_CONTROL_PLANE_URI` for the registry side and `S3_*` for staging bundles.
- No `RAY_ADDRESS` - the caller does not become a Ray driver.

The serve-app's own runtime dependencies (fastapi, transformers, diffusers, ...) are collected by walking the serve-app's import graph in the environment that ran `save_model`, pinned to the versions installed there, and pip-installed on the replica -- minus whatever the ray worker image already bakes in (`worker_provides()`). Pip fetches wheels for the replica's own platform, so compiled dependencies work across laptop and cluster architectures, provided the pinned version is on PyPI. cortexgrid core does not depend on them.

## Pending work

- **Tear-down policy** for idle deployments. Today only explicit `undeploy_model` releases the GPU; consider an idle eviction policy when the registry has more deployable runs than cluster GPUs.
- **Multi-GPU replicas.** A worker gets one GPU, so `num_gpus > 1` cannot be placed until the DaemonSet hands a worker more than one.
- **Stale-bundle GC.** Bundles for undeployed-but-not-deleted runs are not currently garbage-collected. If it becomes a problem, the cleanest signal is "no Serve application currently references this bundle URL"; implement at that point, not before.

## Alternatives that were considered

| Topic | Picked | Rejected | Reason |
|---|---|---|---|
| Serving runtime | Ray Serve | vLLM | vLLM is a second serving stack with patchy arm64; revisit when LLM throughput is a measured problem. |
| Weights source | registry entries kept by the jobs control plane in Postgres + direct S3 writes | MLflow Model Registry; MLflow run artifacts | One indexed lookup per registry read, so a no-op `import_model` / `deploy_model` costs a few round trips to the control plane; MLflow's registry cost a search per read. |
| Library shape | cortexgrid + model-gateway separate | merged | Keeps cortexgrid torch-free for laptop callers; model-gateway stays reusable as a generic LLM client. |
| Endpoint discovery | static URL composed from `RAY_SERVE_URI` + route prefix | jobs-control-plane lookup, MagicDNS, MLflow tag | URL is fully determined by `(family, suffix, run_name)`; no extra state to keep in sync. |
| Caller -> cluster transport | Serve REST API (`/api/serve/applications/`) | (a) `ray.init` + `serve.run` in caller; (b) submit a Ray job that calls `serve.run` | (a) makes the caller a Ray driver - works on Ray nodes / jobs only, breaks on arc-runners; (b) decouples application lifetime from a job lifetime, then we'd have to babysit the job. REST keeps cortexgrid.model_serving HTTP-only and parallels how `cortexgrid.remote` talks to Ray Jobs. |
| Where a replica's hardware needs live | `ModelRequirements` stored as tags on the registry entry | `num_gpus` / `num_replicas` class attributes on the serve-app | The hardware depends on the weights, not on the code fronting them, and the dashboard has to show and edit it - which the class attributes make impossible without importing the serve-app on the cluster. Tags come back with the registry query the dashboard already makes. The replica count is neither: it is a per-deploy choice, so it is an argument to `deploy_model`. |
| How a model's non-weight settings reach the serve-app | a `config` string mapping on the registry entry, read at construction with `model_config` | (a) a fourth `__init__` argument bound by `_serve_entry.build`; (b) one tag per config key | (a) every serve-app's `__init__` would have to grow a parameter, and a bundle frozen before the change binds only three - the migration `ModelRequirements` needed; a lookup with the identifiers the app already holds needs neither. (b) dropping a key would need a tag deletion; one JSON object is replaced in a single write. |
| VRAM accounting | a custom `vram_mib` Ray resource each GPU worker advertises from `nvidia-smi` | cortexgrid picks a node itself and pins the replica to it | Pinning re-implements the scheduler and ties a replica to a node that may be gone by its next restart, and it reserves nothing, so two deploys can both claim the same GPU. A consumable resource both filters and reserves. |
| Preferring the smallest card that fits | a static `vram_mib` node label per size class, ranked with `label_selector` + `fallback_strategy` | (a) rank the nodes in cortexgrid and pin with the `node:<ip>` resource; (b) rank on each node's *free* VRAM, read from `/api/cluster_status` | (a) is the rejected pinning above wearing a different hat: exact, but a replica whose node dies never reschedules. (b) is unnecessary once a fallback is known to fire on exhaustion rather than infeasibility (measured, see [Which GPU it picks](#which-gpu-it-picks-when-several-fit)), and it would depend on that endpoint's camelCased `usageByNode` mangling a custom resource name into `vramMib`. Labels keep Ray the scheduler, keep the reservation where it was, and are read from the state API's unmangled `/api/v0/nodes`. |
| User-facing shape | user writes their own `@cortexgrid.serve.ingress` serve-app; cortexgrid only stores + deploys it | abstract `cortexgrid.Model` + generic `/infer` wrapper | A generic wrapper forces one request/response contract (unary JSON, fixed timeout) on every model. Real models need token streaming, multi-minute diffusion calls, and custom request schemas - all traffic concerns the serve-app must own. Letting cortexgrid own the wrapper collapsed those; the BYO serve-app keeps cortexgrid framework-free and imposes no HTTP shape. |
| Ingress decorator | `cortexgrid.serve.ingress` records the FastAPI app on the class; `_serve_entry.build` applies `ray.serve.ingress` at deploy time, and `_IngressOnReplica` applies it again in each replica | (a) `ray.serve.ingress` at class definition; (b) locate the serve-app through the wrapper's bases (`__mro__`) in `bundle_class` | (a) Ray's wrapper hides the serve-app's module on older Ray, so bundling ships Ray's file instead of the serve-app (see [The serve-app](#the-serve-app)), and importing `ray.serve` needs the `ray[serve]` extras wherever a serve-app is defined. (b) works, but guesses around a Ray implementation detail and still leaves serve-apps importing Ray. Deferring the wrap needs no guessing, works on every Ray version, and serve-apps import only cortexgrid. Its cost: the wrap runs outside the replica, which FastAPI >= 0.137 needs it in, hence the second application (see [On the replica](#on-the-replica)). |
| Bundle timing | bundle the serve-app class at `save_model` time, persist URL+import path as registry tags | bundle at `deploy_model` time from a passed-in `cls` | Save-time bundling lets `deploy_model` callers be stateless - deploy from any process with just `(family, suffix, run_name)`. Re-pairing old weights with a new serve-app requires re-saving (acceptable: it forces an explicit decision and a fresh registry entry). |
| Retrying a failed app | `deploy_model` undeploys a `DEPLOY_FAILED` app and waits until the controller has removed it, then PUTs | (a) PUT the same spec over the failed app; (b) undeploy, then PUT immediately | Ray resets a failed deployment only when a deploy arrives after it was marked for deletion, or when its version changes. (a) keeps the failed deployment: the app reports `DEPLOY_FAILED` again without retrying. (b) races the controller tick that marks the deployments for deletion, with the same result when the PUT wins. |
| Status right after a deploy | read the status as soon as the PUT returns | ignore statuses until the app's `last_deployed_time_s` changes | The PUT is synchronous: the controller registers the app, sets it `DEPLOYING` and stamps `last_deployed_time_s` before responding (checked on Ray 2.9.3 and 2.55), so there is no stale status from a previous attempt to filter out. |
| Code delivery transport | upload to S3, pass via `runtime_env.working_dir` | (a) bake serve-app into cluster image; (b) attach code to the model via MLflow artifacts | (a) cortexgrid doesn't own serve-app classes - they live downstream; baking would invert the dependency. (b) MLflow artifact API is slower per-file and not how Ray Serve consumes `working_dir`. |
