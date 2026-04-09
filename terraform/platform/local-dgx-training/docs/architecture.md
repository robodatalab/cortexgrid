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
    │  cortexflow.init() connects to Ray
    │  cortexflow.remote() submits tasks
    ▼
Ray Head Node (:8265)
    │
    │  Schedules task on available GPU worker
    ▼
Ray Worker (GPU)
    │
    ├── Trains model (PyTorch)
    ├── Logs metrics → MLflow (:5000) → PostgreSQL
    ├── Saves checkpoints → MLflow artifacts → MinIO (:9000)
    │
    │  On failure:
    ├── Task retries (max_retries=3)
    ├── TorchTrainer restarts worker (FailureConfig max_failures=3)
    └── Checkpoints in MinIO enable resume from last saved epoch
```

## Fault Tolerance Layers

| Layer | Mechanism | What It Protects |
|-------|-----------|-----------------|
| Task retry | `@ray.remote(max_retries=3)` | Individual task transient failures |
| Worker restart | `FailureConfig(max_failures=3)` | Training worker crashes |
| Checkpoint resume | MLflow artifact checkpoints | Progress across total failures |
| GCS persistence | Redis-backed GCS | Job queue and cluster state |
| Container restart | `restart: unless-stopped` | DGX reboot / OOM kill |

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
