# MinIO — Argo Deployment

## Problem

Training pipelines, MLflow, and future workloads all need durable object storage for datasets, checkpoints, artifacts, and models. A single in-cluster S3-compatible store gives every workload one endpoint to read and write to, shared across namespaces, without leaving the cluster.

## Components

**stack.yaml** — Argo Application pointing at raw manifests at [`k8s/workloads/minio/`](../../../../../k8s/workloads/minio/). Deployed into the `minio` namespace. See [workloads/minio/README.md](../../../workloads/minio/README.md) for the k8s spec.

## Why raw manifests (not a Helm chart)

Same reasoning as other deployments: one instance, one environment, no templating needs. We initially used the Bitnami MinIO Helm chart but Bitnami deleted the free image tags from Docker Hub in August 2024. The official `minio/minio` image is multi-arch (ARM64 works on DGX), stable, and has no licensing churn — much simpler as a raw Deployment.

## Dependencies

- **MLflow** ([../mlflow/](../../../../../k8s/argo-deployments/aws/mlflow/)) — stores artifacts in bucket `mlflow-artifacts` at `minio.minio.svc.cluster.local:9000`.
- **jobs-control-plane** (future) — will read training data and write checkpoints here via the AWS SDK.
- **Any pod doing S3 I/O** — same endpoint. Creds `admin` / `adminadmin` hardcoded (tailnet-only; tighten when we move to AWS and swap for real S3 + IAM).
