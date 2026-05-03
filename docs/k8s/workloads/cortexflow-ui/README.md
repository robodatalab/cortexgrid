# CortexFlow UI - K8s Spec

How the cortexflow-ui frontend + backend run inside Kubernetes. Applied by the Argo Application at [../../argo-deployments/cortexflow-ui/](../../../../k8s/argo-deployments/cortexflow-ui/). See that folder's README for the higher-level rationale.

## Deployment shape

Two `Deployment`s, one `ClusterIP` Service, one `NodePort` Service. The frontend nginx proxies `/api/*` and `/health` to the backend via the in-cluster DNS name, so the browser only ever hits the frontend NodePort.

- **`cortexflow-ui-backend`** - FastAPI server (`uvicorn cortexflow_ui.backend.main:app`). Reads cluster state via the Kubernetes API (`/api/infra/status`) and reaches Ray/MLflow/S3 via cortexflow library calls.
- **`cortexflow-ui-frontend`** - nginx serving the Vite build, proxying API traffic to the backend Service. No secrets, no cortexflow dependency.

## Why two images instead of one

FastAPI can serve the built frontend via `StaticFiles` (the code at [`cortexflow_ui/backend/main.py:217-218`](../../../../cortexflow_ui/backend/main.py#L217-L218) does), but we split the concerns: the frontend image rebuilds only on frontend changes, the backend image rebuilds only on Python changes. Different base images (`node`->`nginx` vs `python`), different CI triggers, independent rollouts.

## Files

- **deployment-backend.yaml** - backend `Deployment`. Runs as the `cortexflow-ui-backend` ServiceAccount (see role-based-access-control.yaml). `envFrom: aws-creds` injects real AWS credentials so boto3 can call AWS Secrets Manager (which is where every config value the backend needs actually lives). HTTP `/health` probe.
- **deployment-frontend.yaml** - frontend `Deployment`. Pure nginx - no env, no secrets. Probe on `GET /`.
- **service.yaml** - `cortexflow-ui-backend` `ClusterIP:8000` (not browser-reachable on purpose), `cortexflow-ui-frontend` `NodePort:30088`.
- **role-based-access-control.yaml** - `ServiceAccount` `cortexflow-ui-backend` + cluster-wide `ClusterRole`/`ClusterRoleBinding` granting `get`/`list` on `pods` and `get` on `pods/log`. Powers `/api/infra/status`, which queries the Kubernetes API to report pod health across every namespace for the Infrastructure dashboard.

## Where config comes from

Every URL, bucket name, and credential the backend needs lives in AWS Secrets Manager under `robolab/infra/*`. The backend reads them via the cortexflow library:

- `cortexflow.infra.get_mlflow_tracking_uri()` -> `robolab/infra/MLFLOW_TRACKING_URI`
- `cortexflow.infra.get_ray_job_server_uri()` -> `robolab/infra/RAY_JOB_SERVER_URI`
- `cortexflow.s3_util.get_s3_client()` -> `robolab/infra/S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL`
- Bucket name -> `robolab/infra/S3_BUCKET_NAME`

Both the in-cluster service URLs and the browser-facing URLs (returned by `/api/dashboards` so the UI can link out to MLflow/Ray dashboards) are the same SM-stored values - the head's Tailscale IP + NodePort, reachable from both inside and outside the cluster.

The only env in the pod is what `aws-creds` injects: `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` for boto3's default chain to reach AWS Secrets Manager. Everything else flows through cortexflow + SM.

## Port map

| Port | Purpose | Exposure |
|------|---------|----------|
| 80 | Frontend (nginx) | NodePort 30088 (Tailscale-reachable) |
| 8000 | Backend (FastAPI) | in-cluster only (ClusterIP) |
