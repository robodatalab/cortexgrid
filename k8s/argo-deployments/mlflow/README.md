# MLflow

## Problem

Experiments running on Ray need a single place to record parameters, metrics, and artifacts; jobs scheduled by the control plane need a queue of runs to pick up. Without MLflow, every training script would have to implement its own tracking, and comparing runs across experiments would be impossible. MLflow also hosts the model registry used downstream by inference deployments.

## Components

**stack.yaml** — Argo Application installing the Bitnami `mlflow` Helm chart into the `mlflow` namespace. Deploys two services:

- **MLflow tracking server** — the HTTP API + UI (NodePort `:30500`). Reads/writes metadata to Postgres and artifacts to the shared MinIO.
- **PostgreSQL** — metadata backend (runs, experiments, params, metrics).

The chart's bundled MinIO is disabled; artifacts go to the cluster-wide MinIO deployment instead.

## Dependencies

- **MinIO** ([../minio/](../minio/)) — MLflow's artifact store. Tracking server writes to the `mlflow-artifacts` bucket at `minio.minio.svc.cluster.local:9000`.
- **Ray** ([../ray/](../ray/)) — training jobs log to MLflow at `http://mlflow-tracking.mlflow.svc.cluster.local:80` via `MLFLOW_TRACKING_URI`.
- **jobs-control-plane** (future) — polls MLflow for submitted runs and schedules them on Ray. Needs the MLflow URL as an env var.
- **Monitoring** ([../monitoring/](../monitoring/)) — adding a `ServiceMonitor` later will expose tracking-server metrics to Prometheus. Not wired yet.
