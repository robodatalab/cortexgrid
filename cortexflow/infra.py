import os

from cortexflow.secrets import get_secret
from mlflow.tracking import MlflowClient


_MLFLOW_NODEPORT = 30500
_RAY_DASHBOARD_NODEPORT = 30265
_MINIO_API_NODEPORT = 30900


def get_server_ip() -> str:
    dgx_ip = get_secret("DGX_TAILSCALE_IP")
    return dgx_ip


def get_mlflow_run_url(run_id: str) -> str:
    """Build the URL to view a run in the MLflow UI (always via DGX tailscale IP)."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    run = client.get_run(run_id)
    server_ip = get_server_ip()
    return f"http://{server_ip}:{_MLFLOW_NODEPORT}/#/experiments/{run.info.experiment_id}/runs/{run_id}"


def get_mlflow_tracking_uri() -> str:
    if uri := os.environ.get("MLFLOW_TRACKING_URI"):
        return uri
    return f"http://{get_server_ip()}:{_MLFLOW_NODEPORT}"


def get_ray_job_server_uri() -> str:
    if uri := os.environ.get("RAY_JOB_SERVER_URI"):
        return uri
    return f"http://{get_server_ip()}:{_RAY_DASHBOARD_NODEPORT}"


def get_s3_endpoint_url() -> str:
    if uri := os.environ.get("AWS_S3_ENDPOINT_URL"):
        return uri
    return f"http://{get_server_ip()}:{_MINIO_API_NODEPORT}"
