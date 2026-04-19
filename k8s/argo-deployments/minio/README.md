# MinIO

## Problem

Training pipelines, MLflow, and jobs-control-plane all need durable object storage for datasets, checkpoints, artifacts, and models — but standing up S3 buckets for local experimentation is slow, and on-prem egress costs add up. A single in-cluster S3-compatible store gives every workload one endpoint to read and write to, shared across namespaces, without leaving the cluster.

## Components

**stack.yaml** — Argo Application installing the Bitnami `minio` Helm chart into the `minio` namespace. Single-node deployment, 50Gi persistent volume. Exposes:

- S3 API on NodePort `:30900` (used by pods via `minio.minio.svc.cluster.local:9000`).
- Web console on NodePort `:30901` for manual bucket inspection.

Creates one bucket at install time: `mlflow-artifacts`. More buckets can be added by extending `defaultBuckets` in the Helm values.

## Dependencies

- **MLflow** ([../mlflow/](../mlflow/)) — stores artifacts here. MLflow's `externalS3.host` points at `minio.minio.svc.cluster.local`; bundled MinIO inside the MLflow chart is disabled.
- **jobs-control-plane** (future) — will read training data and write checkpoints here via the AWS SDK, pointing `AWS_S3_ENDPOINT_URL` at the same service.
- **Any pod doing S3 I/O** — same endpoint, uses `admin` / `adminadmin` credentials (bootstrap only; rotate via the secrets pipeline once that pattern is needed).
