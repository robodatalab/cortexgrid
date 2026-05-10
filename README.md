# RoboLab Infrastructure

[![cortexflow tests](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-tests.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-tests.yml)
[![cortexflow integration tests](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-integration-tests.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-integration-tests.yml)
[![cortexflow-ui tests](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-ui-tests.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-ui-tests.yml)
[![cortexflow-ui integration tests](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-ui-integration-tests.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-ui-integration-tests.yml)
[![cortexflow-ui-backend](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-ui-backend.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-ui-backend.yml)
[![cortexflow-ui-frontend](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-ui-frontend.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/cortexflow-ui-frontend.yml)
[![jobs-control-plane](https://github.com/robodatalab/robolab-infra/actions/workflows/jobs-control-plane.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/jobs-control-plane.yml)
[![mlflow](https://github.com/robodatalab/robolab-infra/actions/workflows/mlflow.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/mlflow.yml)
[![ray-head](https://github.com/robodatalab/robolab-infra/actions/workflows/ray-head.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/ray-head.yml)
[![version-bump](https://github.com/robodatalab/robolab-infra/actions/workflows/version-bump.yml/badge.svg)](https://github.com/robodatalab/robolab-infra/actions/workflows/version-bump.yml)

> **Run any Python function on your own GPUs — no Dockerfile, no decorator, no commit.**

Cloud infrastructure, ML compute, and deployment orchestration for RoboLab. Cloud resources run on AWS (`eu-west-2`, except the marketing site which stays in `us-east-1` because CloudFront requires `us-east-1` ACM) and are provisioned with Terraform. ML compute runs on a DGX Spark worker that joins the cluster over Tailscale.

## Features

- **Async job submission** — `cortexflow.remote(fn, *args, num_gpus=, num_cpus=, retry=)` ships a Python callable to the cluster and returns a job ID for polling.
- **Experiment tracking** — `Experiment.init(name)` creates an MLflow experiment+run; `log_metric`, `log_params`, `log_artifact` log against it.
- **Checkpoint / resume** — `cortexflow.checkpoint() / resume()` persists training state to MLflow artifacts so retries pick up where the previous attempt left off.
- **Retries** — `retry=True` on `remote()` re-submits failed jobs with the same checkpoint context.
- **Storage** — `upload`, `download`, `upload_dir` against S3 (AWS) or MinIO (on-prem).
- **Secrets** — `get_secret`, `set_secret` against AWS Secrets Manager (AWS) or K8s secrets (on-prem).
- **Two deployment profiles** — same manifests target either AWS (managed Postgres + S3) or on-prem (in-cluster Postgres + MinIO + DGX worker).
- **GitOps cluster bootstrap** — `make head-setup` / `make worker-setup` install k3s and seed Argo CD; the rest syncs from `k8s/`.
- **Experiment UI** — `cortexflow-ui` browses experiments, runs, and job logs (in development).

## Comparison to other platforms

Where cortexflow's surface overlaps with hosted/open-source alternatives. The "Submission" column is the dimension that actually distinguishes them — callable-level (submit a function) vs script-level (lift-and-shift the whole script) vs DAG/class-based.

| Platform | Compute | Submission | Retries | Checkpoint | Tracking | Exp. UI | On-prem | OSS |
|---|---|---|---|---|---|---|---|---|
| **Cortexflow** | Ray + jobs-control-plane | callable | `retry=` flag | auto via MLflow | MLflow | `cortexflow-ui` | yes | yes |
| **ClearML** | agents + queues | script (`execute_remotely`) | yes | manual (artifacts) | yes | yes | yes (self-host) | yes |
| **Determined AI** | yes | `Trial` class | auto | first-class | yes | yes | yes (Helm) | yes |
| **Metaflow / Outerbounds** | yes | `@step` DAG | yes | first-class | yes | yes | yes | yes (Metaflow) |
| **Anyscale** | managed Ray | callable (Ray-native) | Ray-native | via Ray Train | MLflow / W&B | lineage UI | no (cloud) | no |
| **dstack** | yes | task config | yes | manual | no | partial | yes | yes |
| **Modal** | yes | callable (`spawn`) | yes | manual (`modal.Volume`) | no | jobs only | no | no |

**Bottom line:** no platform is a 1:1 drop-in. **ClearML** has the broadest *infrastructure* overlap (queues + agents + tracking + UI + on-prem) but submits scripts, not callables — you'd restructure how work is dispatched. **Anyscale** is the closest *API* match (cortexflow's callable submission is just Ray) but kills on-prem and isn't OSS. **Modal** has the slickest DX but covers only the compute half.

### Code differences

The same toy job — log a metric locally, submit a remote callable that logs another metric to the same experiment — looks different on each platform. The full rewrites of [`cortexflow_examples/jobs/main.py`](cortexflow_examples/jobs/main.py) for each are below.

**1. Defining the remote callable.** Cortexflow and ClearML need no decoration; Modal binds the function to an `App` + `Image`; Anyscale uses Ray's `@ray.remote`.

```python
# Cortexflow
def job_fn():
    cortexflow.log_metric("job_metric", 42.0)

# Modal
@app.function(secrets=[modal.Secret.from_name("mlflow")])
def job_fn(run_id: str): ...

# Anyscale (Ray)
@ray.remote(num_gpus=0)
def job_fn(tracking_uri: str, run_id: str): ...

# ClearML — no decorator; the script is the unit.
# Task.running_locally() splits the local half from the agent half.
```

**2. Submitting the job.**

| Platform | Submit | Returns |
|---|---|---|
| Cortexflow | `cortexflow.remote(job_fn)` | string job ID |
| Modal | `job_fn.spawn(run_id)` | `FunctionCall` |
| Anyscale | `job_fn.remote(uri, run_id)` | `ObjectRef` |
| ClearML | `task.execute_remotely(queue_name="dgx")` | (enqueues, then exits the local process) |

**3. Waiting for completion.** Cortexflow polls Ray via the MLflow lifecycle. Modal and Anyscale block on the handle. ClearML is implicit — the agent runs to completion after the local process has already exited.

```python
# Cortexflow — poll
while True:
    lifecycle = cortexflow.JobLifecycle.load_from_mlflow(exp.run_id, job_id)
    status = cortexflow.get_ray_job_status(lifecycle.get_ray_job_id())
    if status in (cortexflow.JobStatus.FINISHED, cortexflow.JobStatus.FAILED):
        break
    time.sleep(5)

# Modal
call.get(timeout=600)

# Anyscale
ray.get(future)

# ClearML — nothing; local side has already exited at execute_remotely()
```

**4. Experiment tracking.** Cortexflow and ClearML have it built in: both halves of the job log to the same run/task automatically. Modal and Anyscale don't — you bring your own MLflow server and explicitly pass `run_id` (and `tracking_uri`) into the remote callable.

**5. What it actually costs to use.** The mechanism by which each platform ships your code (tarball / image / runtime env / git diff) maps to four user-visible costs:

| | Money | BYO hardware | Time from submit to running | Steps for the very first job |
|---|---|---|---|---|
| **Cortexflow** | $0 software; you pay your own hardware | default | ~10s warm (5s control-plane poll + Ray container start) | write fn → `cortexflow.remote(fn)`. No Dockerfile, no decorator, no commit. |
| **Modal** | per-second metered for compute + GPU; no way to use your own hardware for a discount | not supported (Modal's fleet only) | 1-2s with memory snapshots, 5-30s cold; first image build can take minutes | decorate `@app.function(image=...)`, declare the image inline (pip deps in Python), `modal run` |
| **Anyscale** | AWS/GCP bill + Anyscale management margin | cloud accounts only; true on-prem limited | sub-second on a warm cluster; minutes if a cluster must spin up | configure compute cluster + runtime env, `@ray.remote`, `ray.init("anyscale://...")` |
| **ClearML** | $0 software; you pay your own hardware | default | agent poll (~5-10s) + env recreation from pip freeze (seconds to minutes) | `Task.init()` + `task.execute_remotely()`; *commit and push* so the agent can clone the repo; install `clearml-agent` on the worker |

The asymmetry: Modal asks you to *describe* the environment (in Python). ClearML asks you to *commit* it (to git). Cortexflow uses whatever's in your working directory at submit time — no description, no commit.

Full documentation: [docs/README.md](docs/README.md).
