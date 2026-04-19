import os

from cortexflow.secrets import get_secret
from mlflow.tracking import MlflowClient


_RUNS_ON_SERVER = False


def set_runs_on_server(flag: bool) -> None:
    global _RUNS_ON_SERVER
    _RUNS_ON_SERVER = flag


def runs_on_server() -> bool:
    global _RUNS_ON_SERVER
    return _RUNS_ON_SERVER


def get_server_ip() -> str:
    dgx_ip = get_secret("DGX_TAILSCALE_IP")
    return dgx_ip


def get_mlflow_run_url(run_id: str) -> str:
    """Build the URL to view a run in the MLflow UI (always via DGX tailscale IP)."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    run = client.get_run(run_id)
    server_ip = get_server_ip()
    return (
        f"http://{server_ip}:5000/#/experiments/{run.info.experiment_id}/runs/{run_id}"
    )


def get_mlflow_tracking_uri() -> str:
    """Build the MLflow tracking URI. Prefers MLFLOW_TRACKING_URI env var."""
    if uri := os.environ.get("MLFLOW_TRACKING_URI"):
        return uri
    if runs_on_server():
        return "http://mlflow:5000"
    server_ip = get_server_ip()
    return f"http://{server_ip}:5000" if server_ip else ""


def get_ray_job_server_uri() -> str:
    """Build the Ray job server URI. Prefers RAY_JOB_SERVER_URI env var."""
    if uri := os.environ.get("RAY_JOB_SERVER_URI"):
        return uri
    if runs_on_server():
        return "http://ray-head:8265"
    server_ip = get_server_ip()
    return f"http://{server_ip}:8265" if server_ip else ""


def get_s3_endpoint_url() -> str:
    """Build the S3/MinIO endpoint URL. Prefers AWS_S3_ENDPOINT_URL env var."""
    if uri := os.environ.get("AWS_S3_ENDPOINT_URL"):
        return uri
    if runs_on_server():
        return "http://minio:9000"
    server_ip = get_server_ip()
    return f"http://{server_ip}:9000" if server_ip else ""
