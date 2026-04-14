# Architecture

## Overview

The ML training infrastructure is a set of Docker Compose services running on a DGX Spark, accessible over a Tailscale VPN from a MacBook workstation. The design prioritizes fault tolerance, experiment reproducibility, and seamless AWS expansion.

## Why Redis-Backed Ray GCS

By default, Ray's Global Control Store (GCS) lives in-memory on the head node. If the head node process crashes, all job state — queued jobs, running task metadata, actor handles — is lost.

Backing the GCS with Redis means:
- **Job queue persists across restarts.** Jobs submitted via the Ray Jobs API remain queued even if the head node restarts.
- **Actor state is recoverable.** Actors with `max_restarts` can be re-created after a head crash.
- **Redis itself is durable.** The Redis container uses append-only file persistence (`appendonly yes`) backed by a Docker named volume, surviving container and host restarts.

## Why MinIO (Not Direct Filesystem)

MinIO provides an S3-compatible API locally. Every piece of code that writes artifacts — MLflow, training checkpoints, data generation output — uses `boto3` with an `endpoint_url` from an environment variable.

When moving to AWS:
1. Remove `ARTIFACT_STORE_ENDPOINT` (empty = real S3)
2. Set real AWS credentials
3. Point `ARTIFACT_STORE_BUCKET` at an S3 bucket

No job script changes. No MLflow config changes. The S3 API contract is the abstraction layer.

## Job Flow

```
Mac (your code + cortexflow)
    │
    │  cortexflow.init() reads service URIs from secrets
    │  cortexflow.remote(fn, ...) uploads a payload + JobLifecycle to MLflow
    ▼
MLflow (:5000) — job request queue
    ▲
    │  jobs-control-plane polls every 5s, reconciles against Ray
    │
    ▼
Jobs Control Plane (DGX container)
    │
    │  On each poll cycle:
    │    1. list MLflow JobLifecycle records
    │    2. list Ray submission ids
    │    3. dispatch unmatched jobs via ProcessPoolExecutor
    │    4. stop_ray_job for any job with stop_requested
    │    5. resubmit FAILED attempts for jobs with retry=True
    ▼
Ray Head Node (:8265)
    │
    │  Schedules attempt on available GPU worker; submission id
    │  shape: {run_id}-{job_id}-{attempt}
    ▼
Ray Worker (GPU)
    │
    ├── Runs payload.fn(*payload.args, **payload.kwargs)
    ├── Logs metrics → MLflow (:5000) → PostgreSQL
    ├── Saves checkpoints → MLflow artifacts → MinIO (:9000)
    │
    │  On failure:
    ├── Control plane observes Ray FAILED status and resubmits
    │   (only if the JobLifecycle has retry=True)
    └── cortexflow.checkpoint() + cortexflow.resume() give the next
        attempt a chance to pick up where the last one left off
```

## Reconciliation model

The jobs control plane treats Ray as the single source of truth for execution state. `JobLifecycle` records in MLflow hold only the static identity of a job (`experiment_name`, `run_id`, `job_id`), two latches (`stop_requested`, `error`), and one static flag (`retry`). Status, submission ids, and attempt counters are never persisted — they are derived live by matching `JobLifecycle`s against `list_ray_jobs_with_submission_id()` on every poll.

This has three consequences worth knowing:

- **Retries are unbounded by design.** A `retry=True` job that keeps failing will be resubmitted on every poll for as long as the user lets it. The intended way to terminate a retry loop is to stop the job manually from the UI (or via `cortexflow.stop_experiment_run_jobs`), which flips the `stop_requested` latch; the control plane stops the current Ray attempt on its next poll and short-circuits any in-flight submission worker.
- **Submission-time errors land in `lifecycle.error`.** If a worker raises before reaching Ray (e.g. MLflow is unreachable mid-upload), the control plane captures the exception when it reaps the future and writes the message to `lifecycle.error`. The UI displays whatever the most recent failure was, overwriting any earlier value.
- **Every attempt gets a fresh Ray submission id.** The `-{attempt}` suffix means Ray never sees a duplicate submission; the poll loop can always pick the latest attempt with `max(..., key=get_ray_job_attempt)` and the match function never confuses an old failed attempt for the current one.

## Fault Tolerance Layers

| Layer | Mechanism | What It Protects |
|-------|-----------|-----------------|
| Job retry | `cortexflow.remote(..., retry=True)` — control plane resubmits FAILED Ray attempts with a fresh attempt suffix | Transient worker/GPU failures, crashed dependencies, OOMs |
| Checkpoint resume | `cortexflow.checkpoint()` / `cortexflow.resume()` → MLflow artifacts → MinIO | Progress across retries and restarts |
| GCS persistence | Redis-backed Ray GCS | Job queue and cluster state across Ray head restarts |
| Container restart | `restart: unless-stopped` | DGX reboot / OOM kill |
| Heartbeat healthcheck | `jobs-control-plane` touches `/tmp/cp_heartbeat` each successful poll | Docker restarts the control plane if its poll loop stalls |

## Tailscale Network

All machines (Mac, DGX, future AWS EC2) join the same Tailscale network. This creates a flat, encrypted mesh where:
- Every machine has a stable IP (100.x.x.x)
- No port forwarding or VPN tunnels needed
- No public internet exposure
- Adding a new node is `tailscale up` + `ray start --address=<head_ip>:6379`

```
Mac (100.64.x.1) ◀──── Tailscale mesh ────▶ DGX (100.64.x.2)
                                │
                                ▼
                        EC2 (100.64.x.3)
                        EC2 (100.64.x.4)
                             ...
```

## Service Dependencies

```
redis ──────────────────▶ ray-head
postgres ──────────────┐
minio ─▶ minio-init ──┼──▶ mlflow
node-exporter ─▶ prometheus ─▶ grafana
```

All inter-service communication uses Docker Compose DNS (service names). External access uses the DGX Tailscale IP.
