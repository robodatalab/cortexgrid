# CortexFlow UI — Argo Deployment

## Problem

Running experiments on the DGX means bouncing between Ray's dashboard, MLflow, MinIO, and raw logs — each in its own tab, each with its own URL. Without a single entry point, checking "is job X running, what run did it land in, what did it log" takes three tabs and a grep through `kubectl logs`. CortexFlow UI is a thin FastAPI + React frontend that surfaces experiments, jobs, secrets, and the dashboard links in one place.

## Components

**stack.yaml** — Argo Application pointing at raw k8s manifests at [`k8s/workloads/cortexflow-ui/`](../../workloads/cortexflow-ui/). Deployed into the `cortexflow-ui` namespace. Sync-wave `2` so MLflow, Ray, and MinIO install first. See [workloads/cortexflow-ui/README.md](../../workloads/cortexflow-ui/README.md) for the k8s spec itself.

## Why two images (frontend + backend), not one

FastAPI can serve a built SPA, but splitting them keeps CI triggers narrow: a TypeScript change rebuilds only the frontend image; a Python change rebuilds only the backend. nginx on the frontend side proxies `/api/*` to the backend Service in-cluster, so the browser sees one NodePort URL.

## Dependencies

- **Custom images** — [`../../docker/cortexflow-ui/backend/`](../../docker/cortexflow-ui/backend/) (`ghcr.io/paksas/cortexflow-ui-backend`) and [`../../docker/cortexflow-ui/frontend/`](../../docker/cortexflow-ui/frontend/) (`ghcr.io/paksas/cortexflow-ui-frontend`).
- **MLflow** ([../mlflow/](../mlflow/)) — source of experiments, runs, metrics, and artifacts. Accessed at `http://mlflow.mlflow.svc.cluster.local:5000`.
- **Ray** ([../ray/](../ray/)) — source of job status and logs. Accessed at `http://ray-head.ray.svc.cluster.local:8265`.
- **MinIO** ([../minio/](../minio/)) — artifact bucket read-through. Accessed at `http://minio.minio.svc.cluster.local:9000`.
- **Reflector** ([../secrets/](../secrets/)) — mirrors `aws-creds` (for `cortexflow.secrets`) and `ghcr-pull` (to pull the private images) into this namespace.
- **External Secrets Operator** ([../secrets/](../secrets/)) — materializes `cortexflow-ui-public-urls` from `CONTROL_PLANE_TAILSCALE_IP` at `robolab/infra/*`.
