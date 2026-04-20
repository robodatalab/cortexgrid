# CortexFlow UI — K8s Spec

How the cortexflow-ui frontend + backend run inside Kubernetes. Applied by the Argo Application at [../../argo-deployments/cortexflow-ui/](../../argo-deployments/cortexflow-ui/). See that folder's README for the higher-level rationale.

## Deployment shape

Two `Deployment`s, one `ClusterIP` Service, one `NodePort` Service. The frontend nginx proxies `/api/*` and `/health` to the backend via the in-cluster DNS name, so the browser only ever hits the frontend NodePort.

- **`cortexflow-ui-backend`** — FastAPI server (`uvicorn cortexflow_ui.backend.main:app`). Reads secrets + talks to Ray/MLflow/MinIO from inside the cluster.
- **`cortexflow-ui-frontend`** — nginx serving the Vite build, proxying API traffic to the backend Service. No secrets, no cortexflow dependency.

## Why two images instead of one

FastAPI can serve the built frontend via `StaticFiles` (the code at [`cortexflow_ui/backend/main.py:217-218`](../../../cortexflow_ui/backend/main.py#L217-L218) does), but we split the concerns: the frontend image rebuilds only on frontend changes, the backend image rebuilds only on Python changes. Different base images (`node`→`nginx` vs `python`), different CI triggers, independent rollouts.

## Files

- **deployment-backend.yaml** — backend `Deployment`. `envFrom` pulls `aws-creds` (reflector-mirrored) and `cortexflow-ui-public-urls` (templated below). HTTP `/health` probe.
- **deployment-frontend.yaml** — frontend `Deployment`. Pure nginx — no env, no secrets. Probe on `GET /`.
- **service.yaml** — `cortexflow-ui-backend` `ClusterIP:8000` (not browser-reachable on purpose), `cortexflow-ui-frontend` `NodePort:30088`.
- **secrets.yaml** — `ExternalSecret` `cortexflow-ui-public-urls` templates `PUBLIC_MLFLOW_URL` / `PUBLIC_RAY_DASHBOARD_URL` / `PUBLIC_MINIO_CONSOLE_URL` as `http://<DGX_TAILSCALE_IP>:<NodePort>`. The backend reads these and returns them verbatim from `/api/dashboards` to the browser. `DGX_TAILSCALE_IP` comes from AWS SM (`robolab/infra/DGX_TAILSCALE_IP`).

## Two kinds of URLs

The backend deals with two orthogonal sets of URLs:

- **In-cluster** (`MLFLOW_TRACKING_URI`, `RAY_JOB_SERVER_URI`, `AWS_S3_ENDPOINT_URL`) — where the backend talks to Ray/MLflow/MinIO. Plain env vars on the Deployment, point at `*.svc.cluster.local`.
- **Browser-facing** (`PUBLIC_*_URL`) — what the UI hands back to the user's browser so they can open the MLflow/Ray/MinIO dashboards in a tab. `http://<DGX_IP>:<NodePort>`, reachable over Tailscale.

Keeping them separate means the in-cluster wiring can ignore Tailscale entirely, and the Tailscale-facing URLs only depend on one external input (`DGX_TAILSCALE_IP`), templated once by ESO.

## Port map

| Port | Purpose | Exposure |
|------|---------|----------|
| 80 | Frontend (nginx) | NodePort 30088 (Tailscale-reachable) |
| 8000 | Backend (FastAPI) | in-cluster only (ClusterIP) |

## AWS migration plan

The frontend image is unchanged. The backend drops the `PUBLIC_*_URL` env vars (or repoints them at AWS ALB URLs). `MLFLOW_TRACKING_URI` / `RAY_JOB_SERVER_URI` / `AWS_S3_ENDPOINT_URL` point at the AWS equivalents. The `NodePort` Service becomes an `Ingress` behind an ALB.
