# CortexGrid UI - K8s Spec

How the cortexgrid-ui frontend + backend run inside Kubernetes. Applied by the Argo Application at [../../argo_deployments/cortexgrid_ui/](../../../../k8s/argo_deployments/aws/cortexgrid_ui/). See that folder's README for the higher-level rationale.

## Deployment shape

Two `Deployment`s, one `ClusterIP` Service, one `NodePort` Service. The frontend nginx proxies `/api/*` and `/health` to the backend via the in-cluster DNS name, so the browser only ever hits the frontend NodePort.

- **`cortexgrid-ui-backend`** - FastAPI server (`uvicorn cortexgrid_ui.backend.main:app`). Reads cluster state via the Kubernetes API (`/api/infra/status`) and reaches Ray/MLflow/S3 via cortexgrid library calls.
- **`cortexgrid-ui-frontend`** - nginx serving the Vite build, proxying API traffic to the backend Service. No secrets, no cortexgrid dependency.

## Why two images instead of one

FastAPI can serve the built frontend via `StaticFiles` (the code at [`cortexgrid_ui/backend/main.py:217-218`](../../../../cortexgrid_ui/backend/main.py#L217-L218) does), but we split the concerns: the frontend image rebuilds only on frontend changes, the backend image rebuilds only on Python changes. Different base images (`node`->`nginx` vs `python`), different CI triggers, independent rollouts.

## Files

- **deployment-backend.yaml** - backend `Deployment`. Runs as the `cortexgrid-ui-backend` ServiceAccount (see role-based-access-control.yaml). `CORTEXGRID_HEAD_URL` points `cortexgrid.secrets` at the head secrets server; `envFrom: s3-creds` injects `S3_*` for `cortexgrid.s3_util` to reach the object store. HTTP `/health` probe.
- **deployment-frontend.yaml** - frontend `Deployment`. Pure nginx - no env, no secrets. Probe on `GET /`.
- **service.yaml** - `cortexgrid-ui-backend` `ClusterIP:8000` (not browser-reachable on purpose), `cortexgrid-ui-frontend` `NodePort:30088`.
- **role-based-access-control.yaml** - `ServiceAccount` `cortexgrid-ui-backend` + cluster-wide `ClusterRole`/`ClusterRoleBinding` granting `get`/`list` on `pods` and `get` on `pods/log`. Powers `/api/infra/status`, which queries the Kubernetes API to report pod health across every namespace for the Infrastructure dashboard.

## Where config comes from

Service URLs come from the head secrets server via `cortexgrid.secrets`:

- `cortexgrid.infra.get_mlflow_tracking_uri()` -> `MLFLOW_TRACKING_URI`
- `cortexgrid.infra.get_ray_job_server_uri()` -> `RAY_JOB_SERVER_URI`

Object-storage config comes from the pod env (injected by the `s3-creds` Secret):

- `cortexgrid.s3_util.get_s3_client()` -> `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_ENDPOINT_URL`
- Bucket name -> `S3_BUCKET_NAME`

Both the in-cluster service URLs and the browser-facing URLs (returned by `/api/dashboards` so the UI can link out to MLflow/Ray dashboards) are the same stored values - the head's Tailscale IP + NodePort, reachable from both inside and outside the cluster.

The only secret-related env in the pod is `CORTEXGRID_HEAD_URL` (for `cortexgrid.secrets`) and what `s3-creds` (`S3_*` for `cortexgrid.s3_util`) injects; nothing flows through boto3's default `AWS_*` chain.

## Port map

| Port | Purpose | Exposure |
|------|---------|----------|
| 80 | Frontend (nginx) | NodePort 30088 (Tailscale-reachable) |
| 8000 | Backend (FastAPI) | in-cluster only (ClusterIP) |
