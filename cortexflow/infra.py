from cortexflow.secrets import get_secret
from mlflow.tracking import MlflowClient


def get_mlflow_tracking_uri() -> str:
    return get_secret("MLFLOW_TRACKING_URI")


def get_ray_job_server_uri() -> str:
    return get_secret("RAY_JOB_SERVER_URI")


def get_s3_endpoint_url() -> str:
    # SM holds an empty string on the AWS profile (boto3 then uses real S3),
    # an in-cluster MinIO URL on the on-prem profile. Always written, never absent.
    return get_secret("AWS_S3_ENDPOINT_URL")


def get_mlflow_run_url(run_id: str) -> str:
    base = get_mlflow_tracking_uri()
    client = MlflowClient(tracking_uri=base)
    run = client.get_run(run_id)
    return f"{base}/#/experiments/{run.info.experiment_id}/runs/{run_id}"
