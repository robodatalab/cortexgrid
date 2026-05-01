# MLflow — Argo Deployment

## Problem

Experiments on Ray need a single place to record parameters, metrics, and artifacts; jobs-control-plane reads a queue of submitted runs from MLflow; MLflow also hosts the model registry used downstream by inference. Without it, every training script would implement its own tracking, and comparing runs would be impossible.

## Components

**stack.yaml** — Argo Application pointing at raw k8s manifests at [`k8s/workloads/mlflow/`](../../workloads/mlflow/). Deployed into the `mlflow` namespace. Sync-wave `1`. See [workloads/mlflow/README.md](../../workloads/mlflow/README.md) for the k8s spec.

## Why raw manifests (not a Helm chart)

Same reasoning as [ray/](../ray/): on DGX we have one environment, one MLflow instance, no templating needs. A plain `Deployment` + `Service` is simpler than wiring our custom image into someone else's chart (which we tried with Bitnami's `mlflow` chart — it bundled its own Postgres and MinIO we didn't want). When AWS arrives, we'll likely wrap this in our own Helm chart as per-env values emerge.

## Dependencies

- **Custom image** ([../../docker/mlflow/](../../docker/mlflow/)) — `ghcr.io/paksas/mlflow`. Exact version pins for `mlflow`, `psycopg2-binary`, `boto3`.
- **Postgres (RDS)** — metadata backend, provisioned by [terraform/platform/rds/](../../../terraform/platform/rds/). Connection string is published to AWS Secrets Manager at `robolab/infra/MLFLOW_BACKEND_STORE_URI` and consumed by mlflow's `mlflow-config` ExternalSecret.
- **MinIO** ([../minio/](../minio/)) — artifact store. Bucket `mlflow-artifacts` (auto-created by MinIO's `defaultBuckets`). Accessed as S3 via `MLFLOW_S3_ENDPOINT_URL=http://minio.minio.svc.cluster.local:9000`.
- **Reflector** ([../secrets/](../secrets/)) — provides the `ghcr-pull` Secret in this namespace.
- **Ray** ([../ray/](../ray/)) — training code on Ray workers logs to MLflow at `http://mlflow.mlflow.svc.cluster.local:5000`.
- **Jobs Control Plane** ([../jobs-control-plane/](../jobs-control-plane/)) — polls this MLflow server for submitted runs.
