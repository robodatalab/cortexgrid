import os

from cortexflow.secrets import get_secret
from mlflow.tracking import MlflowClient


def get_mlflow_tracking_uri() -> str:
    """Read MLFLOW_TRACKING_URI from env (in-cluster) or AWS SM (laptop)."""
    if uri := os.environ.get("MLFLOW_TRACKING_URI"):
        return uri
    return get_secret("MLFLOW_TRACKING_URI")


def get_ray_job_server_uri() -> str:
    """Read RAY_JOB_SERVER_URI from env (in-cluster) or AWS SM (laptop)."""
    if uri := os.environ.get("RAY_JOB_SERVER_URI"):
        return uri
    return get_secret("RAY_JOB_SERVER_URI")


def get_s3_endpoint_url() -> str | None:
    """Custom S3 endpoint when AWS_S3_ENDPOINT_URL is set (MinIO scenarios).

    Returns None on AWS so boto3 uses the default `s3.<region>.amazonaws.com`.
    """
    return os.environ.get("AWS_S3_ENDPOINT_URL")


def get_mlflow_run_url(run_id: str) -> str:
    """User-facing URL for viewing a run in the MLflow UI."""
    base = get_mlflow_tracking_uri()
    client = MlflowClient(tracking_uri=base)
    run = client.get_run(run_id)
    return f"{base}/#/experiments/{run.info.experiment_id}/runs/{run_id}"
