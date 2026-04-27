"""PlatformConfig - mirrors .env AWS keys to S3-purposed SM keys.

The cluster's S3 access uses creds from `robolab/infra/S3_ACCESS_KEY_ID/SECRET`
- separate from `AWS_ACCESS_KEY_ID/SECRET` which authenticate Secrets Manager
itself. On the AWS profile the two are functionally identical (S3 IS real AWS,
SM is real AWS), but they are kept separate so the on-prem profile can swap S3
creds for MinIO admin without breaking SM access.

This operator only handles the AWS profile mirror because that value depends
on `.env`. Every other SM key is published by whoever owns the value:

  AWS profile
    S3_BUCKET_NAME, AWS_S3_ENDPOINT_URL  -> terraform/platform/s3
    MLFLOW_BACKEND_STORE_URI             -> terraform/platform/rds

  On-prem profile
    S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
    S3_BUCKET_NAME, AWS_S3_ENDPOINT_URL  -> minio/publish-config Job
    MLFLOW_BACKEND_STORE_URI             -> postgres/publish-config Job

Required deps (setup): profile, aws_access_key_id, aws_secret_access_key.
"""

import logging

from cortexflow.secrets import delete_secret, set_secret
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.platform_config")


_SECRET_S3_ACCESS_KEY_ID = "S3_ACCESS_KEY_ID"
_SECRET_S3_SECRET_ACCESS_KEY = "S3_SECRET_ACCESS_KEY"


class PlatformConfig(Operator):
    def setup(self, deps: dict) -> None:
        if deps["profile"] != "aws":
            # On-prem: the minio + postgres publish-config Jobs handle every
            # SM key. Nothing for this operator to do.
            return
        log.info("Mirroring real-AWS keys to S3_* SM keys...")
        set_secret(_SECRET_S3_ACCESS_KEY_ID, deps["aws_access_key_id"])
        set_secret(_SECRET_S3_SECRET_ACCESS_KEY, deps["aws_secret_access_key"])

    def teardown(self, deps: dict) -> None:
        # Tolerated if missing (delete_secret is idempotent). On the on-prem
        # profile these keys may have been written by minio/publish-config
        # rather than this operator, but deleting on teardown is still
        # appropriate -- the cluster is going away.
        delete_secret(_SECRET_S3_ACCESS_KEY_ID)
        delete_secret(_SECRET_S3_SECRET_ACCESS_KEY)
