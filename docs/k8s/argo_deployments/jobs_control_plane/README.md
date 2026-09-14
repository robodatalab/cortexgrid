# Jobs Control Plane — Argo Deployment

## Problem

Training runs submitted through MLflow need to be picked up and scheduled on Ray. Without a dedicated scheduler, users would have to SSH into the cluster and run `ray job submit` manually for every experiment, and there would be no single place to track what's queued, running, or stuck. Jobs Control Plane polls MLflow for new runs and submits them to the Ray cluster.

## Components

**stack.yaml** — Argo Application pointing at raw k8s manifests at [`k8s/workloads/jobs_control_plane/`](../../../../k8s/workloads/jobs_control_plane/). Deployed into the `jobs-control-plane` namespace. Sync-wave `2` so MLflow and Ray install first. See [workloads/jobs_control_plane/README.md](../../workloads/jobs_control_plane/README.md) for the k8s spec itself.

## Dependencies

- **MLflow** ([../mlflow/](../../../../k8s/argo_deployments/aws/mlflow/)) — polled for submitted runs. URL set via `MLFLOW_TRACKING_URI`.
- **Ray** ([../ray/](../../../../k8s/argo_deployments/aws/ray/)) — target scheduler. Connection via `RAY_ADDRESS` (Ray client port on the head service).
- **Reflector** ([../secrets/](../../../../k8s/argo_deployments/base/secrets/)) — mirrors the cluster-wide `s3-creds` and `ghcr-pull` Secrets into this namespace. The Deployment consumes `s3-creds` via `envFrom`, references `ghcr-pull` in `imagePullSecrets`, and reads everything else from the head secrets server at `CORTEXGRID_HEAD_URL`.
