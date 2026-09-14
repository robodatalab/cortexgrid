# CortexGrid UI — Argo Deployment

## Problem

Running experiments on the DGX means bouncing between Ray's dashboard, MLflow, MinIO, and raw logs — each in its own tab, each with its own URL. Without a single entry point, checking "is job X running, what run did it land in, what did it log" takes three tabs and a grep through `kubectl logs`. CortexGrid UI is a thin FastAPI + React frontend that surfaces experiments, jobs, secrets, and the dashboard links in one place.

## Components

**stack.yaml** — Argo Application pointing at raw k8s manifests at [`k8s/workloads/cortexgrid_ui/`](../../../../k8s/workloads/cortexgrid_ui/). Deployed into the `cortexgrid-ui` namespace. Sync-wave `2` so MLflow, Ray, and MinIO install first. See [workloads/cortexgrid_ui/README.md](../../workloads/cortexgrid_ui/README.md) for the k8s spec itself.

## Why two images (frontend + backend), not one

FastAPI can serve a built SPA, but splitting them keeps CI triggers narrow: a TypeScript change rebuilds only the frontend image; a Python change rebuilds only the backend. nginx on the frontend side proxies `/api/*` to the backend Service in-cluster, so the browser sees one NodePort URL.

## Dependencies

- **Custom images** — [`../../docker/cortexgrid_ui/backend/`](../../../../k8s/docker/cortexgrid_ui/backend/) (`ghcr.io/robodatalab/cortexgrid-ui-backend`) and [`../../docker/cortexgrid_ui/frontend/`](../../../../k8s/docker/cortexgrid_ui/frontend/) (`ghcr.io/robodatalab/cortexgrid-ui-frontend`).
- **MLflow** ([../mlflow/](../../../../k8s/argo_deployments/aws/mlflow/)) — source of experiments, runs, metrics, and artifacts. Accessed at `http://mlflow.mlflow.svc.cluster.local:5000`.
- **Ray** ([../ray/](../../../../k8s/argo_deployments/aws/ray/)) — source of job status and logs. Accessed at `http://ray-head.ray.svc.cluster.local:8265`.
- **MinIO** ([../minio/](../../../../k8s/argo_deployments/onprem/minio/)) — artifact bucket read-through. Accessed at `http://minio.minio.svc.cluster.local:9000`.
- **Reflector** ([../secrets/](../../../../k8s/argo_deployments/base/secrets/)) - mirrors `s3-creds` (object-storage identity) and `ghcr-pull` (to pull the private images) into this namespace.
- **Head secrets server** - service URLs and bucket names are read by the backend via cortexgrid library calls to `CORTEXGRID_HEAD_URL`, not pre-rendered into env.
