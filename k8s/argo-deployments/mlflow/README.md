# MLflow

## Problem

Experiments running on Ray need a single place to record parameters, metrics, and artifacts; jobs scheduled by the control plane need a queue of runs to pick up. Without MLflow, every training script would have to implement its own tracking, and comparing runs across experiments would be impossible. MLflow also hosts the model registry used downstream by inference deployments.

## Components

**stack.yaml** — Argo Application installing the Bitnami `mlflow` Helm chart into the `mlflow` namespace. The chart bundles three services that MLflow needs:

- **MLflow tracking server** — the HTTP API + UI (NodePort `:30500`). Speaks to Postgres for metadata and MinIO (S3) for artifacts.
- **PostgreSQL** — metadata backend (runs, experiments, params, metrics).
- **MinIO** — S3-compatible artifact store (models, logged files, plots). Bundled so MLflow works without depending on external S3.

## Dependencies

- **Ray** ([../ray/](../ray/)) — training jobs running on Ray log into MLflow at `http://mlflow-tracking.mlflow.svc.cluster.local:80` using `MLFLOW_TRACKING_URI`.
- **jobs-control-plane** (future) — polls MLflow for submitted runs and schedules them on Ray. Needs MLflow URL as env var.
- **Monitoring** ([../monitoring/](../monitoring/)) — adding a `ServiceMonitor` later will expose tracking-server metrics to Prometheus. Not wired yet.
