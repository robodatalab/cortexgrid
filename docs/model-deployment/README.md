# Model deployment

> **Status:** proposed, not yet implemented. Date: 2026-05-09. All code references in this doc are hypothetical until implemented.

## Problem

cortexflow trains models and stores them in MLflow + MinIO. There is no path today to serve a trained model for inference, fine-tune it further from a deployed checkpoint, or address it from application code through a stable API. We also want to keep model-gateway as the single facade application code uses to talk to *any* model (Anthropic, local HuggingFace, or a model trained on our infra).

## Goals

- Application code uses one library (model-gateway) to address every model, regardless of where the model runs or where its weights came from.
- A trained run can be served with a single call, by run id, with no manual weight copying or hand-rolled k8s manifests per model.
- The serving path reuses the cluster components we already run (Ray, MLflow, MinIO) and stays behind tailscale, like everything else.
- cortexflow's lean clients (laptop callers that only list runs or submit jobs) stay free of torch.
- onprem and AWS profiles serve identically; no arch-specific image (the cluster is mixed arm64/amd64 - DGX Spark + ThinkStation P5).

## Non-goals

- High-throughput LLM serving with continuous batching. Not adopting vLLM in this iteration; deferred until throughput is a measured problem.
- A formal model registry with promotion aliases (`@prod`, `@staging`). Deployments addressed by `run_id`; promotion is convention until it hurts.
- An OpenAI-compatible HTTP surface. The wire protocol is internal; model-gateway types (`CompletionChunk`, `ToolCall`) are the contract.
- A separate "models" S3 bucket distinct from MLflow's artifact store. MLflow already places weights in MinIO; the run carries the pointer.
- Merging cortexflow and model-gateway into one library.

## Decision summary

| Choice | Decision | One-line reason |
|--------|----------|-----------------|
| Serving runtime | **Ray Serve** | Ray is already deployed; no new image stack; no arm64 caveat. |
| Weights source | **MLflow run artifacts (run_id)** | Zero training-side change; symmetrical with how cortexflow already keys off run_id. |
| Library shape | **Keep cortexflow and model-gateway separate** | Lean cortexflow callers stay torch-free; model-gateway remains a generic LLM client. |
| Facade for callers | **model-gateway** | One mental model for application code; new `robolab:<run_id>` provider. |
| Inference server code | **model-gateway (`model_gateway.serving`)** | model-gateway already owns the wire protocol and the HuggingFaceModel engine. |
| Endpoint discovery | **cortexflow + jobs-control-plane** | Discovery is a job-lifecycle question; the control plane already exists. |

## Dependencies

- Ray + Ray Serve - already deployed, see [k8s/workloads/ray/](../../k8s/workloads/ray/)
- MLflow + MinIO - already deployed, see [k8s/workloads/mlflow/](../../k8s/workloads/mlflow/) and [k8s/workloads/minio/](../../k8s/workloads/minio/)
- Tailscale - already in place; serving endpoints are k8s Services reached over the tailnet
- jobs-control-plane - extended with deploy/list/undeploy routes, see [k8s/workloads/jobs-control-plane/](../../k8s/workloads/jobs-control-plane/)

## Architecture

### Topology

```
laptop / app code
  |
  +-- import cortexflow                          (lean: mlflow, boto3, jobs-control-plane client)
  |    cortexflow.deploy(run_id) -- HTTPS --> jobs-control-plane
  |                                                 |
  |                                                 v
  |                                          schedules a Ray Serve app
  |                                          targeting the run's artifact uri
  |
  +-- import model_gateway                     (lean: anthropic provider, RobolabModel HTTP client)
       cortexflow.deploy(run_id) returns a RobolabModel
       model_gateway.complete(model, ...) ----HTTPS---> Ray Serve replica
                                                            |
                                                            | (in-cluster pod, has torch)
                                                            | model_gateway.serving wraps
                                                            | model_gateway.HuggingFaceModel
                                                            | weights pulled once at start from
                                                            | s3://mlflow/<exp>/<run_id>/artifacts/
                                                            v
                                                          MinIO
```

### Wire protocol

The Ray Serve replica exposes one route, `POST /complete`, taking a JSON body with the same shape as `CompletingModel.complete` (`messages`, optional `tools` as JSON specs, `max_new_tokens`, `temperature`, free-form `**kwargs`). Response is an NDJSON stream where each line is a serialized `CompletionChunk`. Tool calls inside chunks carry `id`, `name`, and `arguments`; the client side rebuilds `ToolCall` objects, where `_func` is bound on the caller's side from the tools the caller passed in (the server never executes tools - it only emits the model's intent to call them).

### Library responsibilities

**cortexflow (laptop side)**
- Submits and observes deployments via the jobs-control-plane HTTP API, the same way `schedule_remote_job` already does for training jobs.
- Returns `Deployment` records (run_id, url, status, num_replicas) and a convenience `RobolabModel` instance ready to use with model-gateway.
- Never imports torch or transformers; never imports model-gateway from laptop code paths (model-gateway is imported only by the in-cluster serving runtime).

**jobs-control-plane (in-cluster)**
- Gains routes `POST /deployments`, `DELETE /deployments/<run_id>`, `GET /deployments`, `GET /deployments/<run_id>`.
- Idempotent on `run_id`: a second `POST` for the same run id returns the existing deployment unchanged.
- Translates a deployment request into a Ray Serve application: image is the prebuilt `model-gateway[serving]` image, entrypoint is `python -m model_gateway.serving --run-id <id>`, replica resources include `num_gpus`, scheduling pins `role=head` per the existing workload placement convention.

**model-gateway (laptop side)**
- New file `model_gateway/providers/robolab.py` adds `RobolabModel(CompletingModel)` and registers the `robolab:` prefix.
- `RobolabModel` is a pure HTTP client: no torch, no transformers, only `httpx`.
- The `robolab:` factory resolves run_id to URL by calling `cortexflow.get_endpoint(run_id)`. This makes cortexflow a runtime dep of the `robolab` provider, exposed via a `[robolab]` extra.

**model-gateway (in-cluster serving runtime)**
- New package `model_gateway/serving/` runs inside the Ray Serve replica.
- On startup: downloads the run's artifact directory from MinIO to a local path, instantiates `HuggingFaceModel(local_path=...)`, and starts a FastAPI server with `/complete` streaming NDJSON.
- This is the only path that pulls torch + transformers; gated behind a `[serving]` extra and used by the Ray Serve image build.

## Caller-facing API

```python
import cortexflow, model_gateway

# Schedule (or attach to an existing) deployment for a trained run.
model = cortexflow.deploy(run_id)            # returns a model_gateway.RobolabModel

# Use it like any other model-gateway model.
async for chunk in model_gateway.complete(model, messages):
    print(chunk.content, end="")

# Or address an already-running deployment by id, without going through cortexflow:
model = model_gateway.deploy_model(f"robolab:{run_id}")
```

## Proposed API surface

All snippets describe code that does not yet exist.

### cortexflow

```python
# cortexflow/serve.py (new)

@dataclass
class Deployment:
    run_id: str
    url: str
    status: str           # "pending" | "running" | "failed" | "stopping" | "stopped"
    num_replicas: int

def list_deployable_runs() -> list[RunSummary]: ...
def list_active_deployments() -> list[Deployment]: ...

def deploy(run_id: str, num_gpus: int = 1) -> "model_gateway.RobolabModel":
    """Idempotent on run_id. Returns a ready-to-use model-gateway model."""

def undeploy(run_id: str) -> None: ...
def get_endpoint(run_id: str) -> str | None: ...
```

`deploy()` constructs the `RobolabModel` by importing `model_gateway` at module-import time of `cortexflow.serve` (per the project's no-local-imports rule). To preserve the "lean cortexflow caller stays torch-free" guarantee: cortexflow declares `model-gateway` as an optional dep (extra `[serve]`), and `cortexflow.serve` is not imported from `cortexflow/__init__.py`. Callers who want serving do `from cortexflow.serve import deploy`.

### model-gateway

```python
# model_gateway/providers/robolab.py (new)

class RobolabModel(CompletingModel):
    def __init__(self, name: str, base_url: str): ...
    @property
    def name(self) -> str: ...
    async def complete(self, messages, tools=None, max_new_tokens=2048,
                       temperature=0.7, **kwargs) -> AsyncIterator[CompletionChunk]: ...

def deploy_robolab(model_id: str) -> RobolabModel | None:
    """Resolves robolab:<run_id> to a RobolabModel via cortexflow.get_endpoint."""

# Registered at import time:
register_provider("robolab:", deploy_robolab)
```

```python
# model_gateway/serving/__main__.py (new, in-cluster only)

def main():
    args = parse_args()       # --run-id, --port
    local = pull_run_artifacts_to_local(args.run_id)
    engine = HuggingFaceModel(name=f"robolab:{args.run_id}", local_path=local)
    serve_http(engine, port=args.port)
```

`serve_http` is a thin FastAPI app with one POST route streaming NDJSON. It works for any `CompletingModel`, not just HF; future in-cluster providers slot in here without changes to the wire protocol.

## Repos and what changes in each

| Repo | New code | Purpose |
|------|----------|---------|
| robolab-infra (cortexflow) | `cortexflow/serve.py` | `deploy / undeploy / list_active_deployments / list_deployable_runs / get_endpoint`, all backed by jobs-control-plane |
| robolab-infra (k8s) | `k8s/workloads/jobs-control-plane/` route additions | `POST /deployments`, `DELETE /deployments/<run_id>`, `GET /deployments` |
| model-gateway | `model_gateway/providers/robolab.py` | `RobolabModel(CompletingModel)` HTTP client + `robolab:` prefix registration |
| model-gateway | `model_gateway/serving/` | FastAPI wrapper around any `CompletingModel`, used as Ray Serve replica entrypoint |
| model-gateway | `pyproject.toml` extras | `[robolab]` (depends on cortexflow), `[serving]` (torch + transformers + fastapi, used inside the Ray Serve image) |

## Install profiles

| Profile | Install | What it gets |
|---------|---------|--------------|
| Lean caller (Anthropic only, or remote robolab models) | `pip install model-gateway[robolab]` | Anthropic + RobolabModel HTTP client + cortexflow for endpoint resolution. **No torch.** |
| Local HF + Anthropic, no robolab | `pip install model-gateway` | Today's behavior. |
| Ray Serve image (in-cluster) | `model-gateway[serving]` | Adds torch + transformers + fastapi for the inference server. |

## Alternatives considered

### Serving runtime

**vLLM:** rejected for now. vLLM consumes HF format directly (so the LoRA-on-HF format produced in `model-training/model_training/sft.py` is serveable, contrary to a first-pass assumption that compression/encoding would be needed). The real costs are: a second serving stack alongside Ray; arm64 wheels are patchy, conflicting with the cluster's mixed arm64/amd64 makeup; cortexflow-ui would need to learn a new resource type. Revisit when LLM throughput under concurrent load is a measured problem.

**FastAPI + transformers (no Ray Serve):** rejected. Reimplements batching/streaming/queuing/autoscaling that Ray Serve gives us for free, on top of an already-deployed Ray cluster.

### Weights source

**MLflow Model Registry:** rejected for now. Adds a training-side change (`mlflow.register_model` after each training run) and a second user-facing surface (the Registry UI) that competes with cortexflow-ui. Revisit when promotion-by-convention starts hurting; the upgrade path is mechanical.

**Dedicated `s3://robolab-models/` bucket:** rejected. MLflow's artifact store *is* MinIO; weights are already in S3 and the run carries the pointer. A separate bucket would duplicate the placement decision MLflow already makes.

### Library shape: merge cortexflow and model-gateway

Considered and rejected. Strongest argument for merge: symmetry (`cortexflow.serve()` returning a `cortexflow.RobolabModel` reads as one library). Blocking argument against: dependency weight - model-gateway pulls torch and transformers via its in-process HuggingFace provider, and merging would bleed those into every cortexflow caller. Python extras would mitigate but not eliminate the friction, and model-gateway is plausibly reusable outside this stack as a standalone Anthropic+HF client. Revisit if model-gateway has no external consumers.

### Endpoint discovery

**MagicDNS convention** (e.g., `serve-<run_id>.cortexflow.tailnet`): rejected. Avoids a control-plane route but couples deployment lifecycle to DNS records that have to stay in sync.

**MLflow tag** (deployment URL written to the run's tags): rejected. Conflates "deployment state" (mutates) with "metrics store" (treated as append-only history).

## Open questions

1. **Deployable marker.** MLflow tag (`deployable=true` set by training) vs. structure detection on the run's artifact directory (e.g., `model/` subdir with `config.json`). Tag is explicit but adds a training-side call; structure-detection avoids the call but couples discovery to the artifact layout. Tag is the leaning choice.
2. **Concurrency on `deploy(run_id)`.** Two callers deploy the same run id concurrently: control plane should return a single shared deployment. Reference-counted undeploy vs. last-writer-wins is open; reference-counting is safer if multiple callers can request the same model.
3. **Tear-down policy.** Idle eviction (control plane stops a deployment after N minutes with no requests) vs. explicit `undeploy` only. Idle eviction is cheaper but introduces a "first request after eviction is slow" failure mode.
4. **Multi-tenant scheduling.** When more deployments are requested than the cluster has GPUs for: queue, reject, or evict the least-recently-used? Out of scope for the first iteration (assume small N).

## Implementation plan

Each step is independently shippable.

1. **Wire protocol freeze.** Define the `/complete` request/response schema (NDJSON of `CompletionChunk`) in `model_gateway/serving/protocol.py`. Includes tool-call serialization rules and chunk finish-reason values.
2. **`model_gateway.serving` (in-cluster server).** FastAPI wrapper + entrypoint. Tested locally against a pre-downloaded HF model directory before any cluster integration.
3. **Ray Serve image build.** Container that installs `model-gateway[serving]` and starts the entrypoint. Built and pushed via existing CI per the image tagging convention.
4. **`RobolabModel` + `robolab:` provider in model-gateway.** Pure HTTP client, no torch path. Tested against a local instance of step 2.
5. **jobs-control-plane routes.** `POST /deployments`, `GET`, `DELETE`. Idempotent on run_id. Wires Ray Serve app submission.
6. **`cortexflow.serve` module.** Thin client over the new control-plane routes, plus `deploy()` returning a ready `RobolabModel`.
7. **cortexflow-ui surface.** "Deployments" tab listing active deployments, deploy button on a run row. Pulls from the new control-plane routes.
8. **Onprem and AWS parity smoke test.** Deploy the same run on both profiles, hit it from a laptop over tailscale, verify round-trip.

## Related docs

- [cortexflow](../cortexflow/README.md) - existing library overview
- [k8s](../k8s/README.md) - cluster topology
