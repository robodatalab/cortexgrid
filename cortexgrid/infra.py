from cortexgrid.secrets import get_secret
from mlflow.tracking import MlflowClient


def get_mlflow_tracking_uri() -> str:
    return get_secret("MLFLOW_TRACKING_URI")


def get_ray_job_server_uri() -> str:
    return get_secret("RAY_JOB_SERVER_URI")


def get_ray_serve_uri() -> str:
    """HTTP base URL where Ray Serve apps are reachable (data plane, port 8000)."""
    return get_secret("RAY_SERVE_URI")


def get_ray_serve_applications_uri() -> str:
    """Dashboard REST endpoint for declarative Serve app management."""
    return f"{get_ray_job_server_uri()}/api/serve/applications/"


def get_s3_endpoint_url() -> str:
    # AWS profile: regional s3.amazonaws.com URL (stored as "" = no override).
    # On-prem: tailnet-reachable MinIO NodePort URL.
    return get_secret("S3_ENDPOINT_URL")


def get_s3_bucket() -> str:
    # Provisioned by terraform/platform/s3 on AWS; created lazily on first
    # upload against on-prem MinIO.
    return get_secret("S3_BUCKET_NAME")


def get_mlflow_run_url(run_id: str) -> str:
    base = get_mlflow_tracking_uri()
    client = MlflowClient(tracking_uri=base)
    run = client.get_run(run_id)
    return f"{base}/#/experiments/{run.info.experiment_id}/runs/{run_id}"
