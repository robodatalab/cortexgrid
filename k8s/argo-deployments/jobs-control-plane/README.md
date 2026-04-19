# Jobs Control Plane — Argo Deployment

## Problem

Training runs submitted through MLflow need to be picked up and scheduled on Ray. Without a dedicated scheduler, users would have to SSH into the cluster and run `ray job submit` manually for every experiment, and there would be no single place to track what's queued, running, or stuck. Jobs Control Plane polls MLflow for new runs and submits them to the Ray cluster.

## Components

**stack.yaml** — Argo Application pointing at raw k8s manifests at [`k8s/workloads/jobs-control-plane/`](../../workloads/jobs-control-plane/). Deployed into the `jobs-control-plane` namespace. Sync-wave `2` so MLflow and Ray install first. See [workloads/jobs-control-plane/README.md](../../workloads/jobs-control-plane/README.md) for the k8s spec itself.

## Dependencies

- **MLflow** ([../mlflow/](../mlflow/)) — polled for submitted runs. URL set via `MLFLOW_TRACKING_URI`.
- **Ray** ([../ray/](../ray/)) — target scheduler. Connection via `RAY_ADDRESS` (Ray client port on the head service).
- **External Secrets Operator** ([../secrets/](../secrets/)) — materializes `aws-creds` from AWS Secrets Manager.
- **Reflector** ([../secrets/](../secrets/)) — provides the `ghcr-pull` Secret in this namespace so the Deployment can pull the private image from GHCR.
