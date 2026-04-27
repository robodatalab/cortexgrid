"""PlatformConfig — writes profile-specific platform config to AWS SM.

The cluster's mlflow + boto3-using workloads read their backend store URI,
S3 bucket/credentials, and S3 endpoint override from `robolab/infra/*`. The
contents differ between the AWS and on-prem profiles:

  AWS profile
    S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY ← mirror of .env AWS_* (real S3)
    S3_BUCKET_NAME                          ← written by terraform/platform/s3
    MLFLOW_BACKEND_STORE_URI                ← written by terraform/platform/rds
    AWS_S3_ENDPOINT_URL                     ← the real-AWS S3 regional endpoint;
                                              functionally equivalent to passing
                                              no endpoint to boto3, but written
                                              explicitly so SM has the key.

  On-prem profile
    S3_ACCESS_KEY_ID = "admin"              ← MinIO admin user
    S3_SECRET_ACCESS_KEY = "adminadmin"     ← MinIO admin password
    S3_BUCKET_NAME = "mlflow-artifacts"     ← bucket created by minio bucket-init Job
    MLFLOW_BACKEND_STORE_URI                ← in-cluster Postgres
    AWS_S3_ENDPOINT_URL                     ← in-cluster MinIO Service

Required deps (setup): profile.
"""

import logging

from cortexflow.secrets import delete_secret, set_secret
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.platform_config")


_SECRET_S3_ACCESS_KEY_ID = "S3_ACCESS_KEY_ID"
_SECRET_S3_SECRET_ACCESS_KEY = "S3_SECRET_ACCESS_KEY"
_SECRET_S3_BUCKET_NAME = "S3_BUCKET_NAME"
_SECRET_MLFLOW_BACKEND_STORE_URI = "MLFLOW_BACKEND_STORE_URI"
_SECRET_AWS_S3_ENDPOINT_URL = "AWS_S3_ENDPOINT_URL"

_ONPREM_POSTGRES_URI = (
    "postgresql://admin:admin@postgres.postgres.svc.cluster.local:5432/mlflow"
)
_ONPREM_MINIO_ENDPOINT = "http://minio.minio.svc.cluster.local:9000"
_ONPREM_MINIO_BUCKET = "mlflow-artifacts"
_ONPREM_MINIO_ACCESS_KEY = "admin"
_ONPREM_MINIO_SECRET_KEY = "adminadmin"

# Default real-AWS S3 endpoint matching the project's eu-west-2 region. boto3
# treats this as identical to the no-endpoint default for that region.
_AWS_S3_ENDPOINT_URL = "https://s3.eu-west-2.amazonaws.com"


class PlatformConfig(Operator):
    def setup(self, deps: dict) -> None:
        profile = deps["profile"]
        if profile == "aws":
            self._setup_aws(deps)
        else:
            self._setup_onprem()

    def teardown(self, deps: dict) -> None:
        # Delete every key this operator may have written, regardless of profile —
        # teardown shouldn't need to re-detect profile, and missing keys are tolerated
        # by delete_secret.
        for key in (
            _SECRET_S3_ACCESS_KEY_ID,
            _SECRET_S3_SECRET_ACCESS_KEY,
            _SECRET_S3_BUCKET_NAME,
            _SECRET_MLFLOW_BACKEND_STORE_URI,
            _SECRET_AWS_S3_ENDPOINT_URL,
        ):
            delete_secret(key)

    def _setup_aws(self, deps: dict) -> None:
        log.info("Mirroring real-AWS S3 credentials to S3_* SM keys...")
        set_secret(_SECRET_S3_ACCESS_KEY_ID, deps["aws_access_key_id"])
        set_secret(_SECRET_S3_SECRET_ACCESS_KEY, deps["aws_secret_access_key"])
        # Real-AWS regional S3 endpoint. boto3 with this URL hits real S3
        # exactly the same as if no endpoint were passed; storing the real URL
        # avoids the AWS-SM "min length 1" constraint that disallows empty
        # strings as sentinels.
        set_secret(_SECRET_AWS_S3_ENDPOINT_URL, _AWS_S3_ENDPOINT_URL)

    def _setup_onprem(self) -> None:
        log.info("Writing on-prem platform config (MinIO + in-cluster Postgres) to SM...")
        set_secret(_SECRET_S3_ACCESS_KEY_ID, _ONPREM_MINIO_ACCESS_KEY)
        set_secret(_SECRET_S3_SECRET_ACCESS_KEY, _ONPREM_MINIO_SECRET_KEY)
        set_secret(_SECRET_S3_BUCKET_NAME, _ONPREM_MINIO_BUCKET)
        set_secret(_SECRET_MLFLOW_BACKEND_STORE_URI, _ONPREM_POSTGRES_URI)
        set_secret(_SECRET_AWS_S3_ENDPOINT_URL, _ONPREM_MINIO_ENDPOINT)
