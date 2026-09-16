# MinIO — Argo Deployment

## Problem

Training pipelines, MLflow, and future workloads all need durable object storage for datasets, checkpoints, artifacts, and models. A single in-cluster S3-compatible store gives every workload one endpoint to read and write to, shared across namespaces, without leaving the cluster.

## Components

**stack.yaml** — Argo Application pointing at raw manifests at [`k8s/workloads/minio/`](../../../../../k8s/workloads/minio/). Deployed into the `minio` namespace. See [workloads/minio/README.md](../../../workloads/minio/README.md) for the k8s spec.

## Why raw manifests (not a Helm chart)

Same reasoning as other deployments: one instance, one environment, no templating needs. We initially used the Bitnami MinIO Helm chart but Bitnami deleted the free image tags from Docker Hub in August 2024. The official MinIO image is multi-arch (ARM64 works on DGX) and much simpler as a raw Deployment. MinIO stopped publishing community images in October 2025 and deleted `minio/minio` and `minio/mc` from Docker Hub in September 2026, so the chart pulls the same tags from `quay.io/minio/`. Those tags are frozen and get no security fixes, so MinIO will eventually need replacing.

## Dependencies

- **MLflow** ([../mlflow/](../../../../../k8s/argo_deployments/aws/mlflow/)) — stores artifacts in bucket `mlflow-artifacts` at `minio.minio.svc.cluster.local:9000`.
- **jobs-control-plane** (future) — will read training data and write checkpoints here via the AWS SDK.
- **Any pod doing S3 I/O** — same endpoint. Creds `admin` / `adminadmin` hardcoded (tailnet-only; tighten when we move to AWS and swap for real S3 + IAM).
