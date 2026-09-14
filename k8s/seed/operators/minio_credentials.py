"""MinioCredentials -- on-prem only.

Mirrors what terraform/platform/s3 does for AWS: the *infrastructure layer*
(terraform on AWS, this seed operator on on-prem) is the publisher of MinIO/S3
service-discovery into AWS Secrets Manager. The workload layer (mlflow,
cortexgrid library, CI runners) only reads from SM and stays profile-agnostic.

Generates a random MinIO admin password on first run, persists it in the
in-cluster minio-credentials Secret (so MinIO can boot via
MINIO_ROOT_USER/PASSWORD), and publishes:
  - S3_ENDPOINT_URL       = http://<head-tailscale-ip>:30900
  - S3_REGION             = us-east-1 (MinIO ignores it; boto3 needs a value)
  - S3_BUCKET_NAME        = mlflow-artifacts
  - S3_ACCESS_KEY_ID      = <user>
  - S3_SECRET_ACCESS_KEY  = <password>

The endpoint URL uses the head's tailscale IP + the MinIO Service NodePort so
the same value works for in-cluster pods (flannel binds to tailscale0) and
for any tailnet member -- laptops, CI runners, ray workers. This is the
on-prem analog of terraform's `https://s3.<region>.amazonaws.com`, which
is universally reachable on the AWS profile.

Idempotent: on rerun, reads the existing password from the cluster Secret
rather than regenerating, so MinIO data tied to those creds is preserved.

Required deps (setup): node_ip.
Required deps (teardown): (none).
"""

import base64
import logging
import secrets
import subprocess
import textwrap

from cortexgrid.secrets import delete_secret, set_secret
from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.minio_credentials")


_NAMESPACE = "cortexgrid"
_SECRET_NAME = "minio-credentials"
_USER = "admin"
_BUCKET = "mlflow-artifacts"
# MinIO is region-agnostic; boto3's SigV4 still requires a region string,
# so any well-formed AWS region works. us-east-1 is the standard placeholder
# in MinIO docs.
_REGION = "us-east-1"
_SECRET_S3_ENDPOINT_URL = "S3_ENDPOINT_URL"
_SECRET_S3_REGION = "S3_REGION"
_SECRET_S3_BUCKET_NAME = "S3_BUCKET_NAME"
_SECRET_S3_ACCESS_KEY_ID = "S3_ACCESS_KEY_ID"
_SECRET_S3_SECRET_ACCESS_KEY = "S3_SECRET_ACCESS_KEY"


class MinioCredentials(Operator):
    def setup(self, deps: dict) -> None:
        password = self._get_or_generate_password()
        self._apply_secret(password)
        self._publish(deps["node_ip"], password)

    def teardown(self, deps: dict) -> None:
        delete_secret(_SECRET_S3_ENDPOINT_URL)
        delete_secret(_SECRET_S3_REGION)
        delete_secret(_SECRET_S3_BUCKET_NAME)
        delete_secret(_SECRET_S3_ACCESS_KEY_ID)
        delete_secret(_SECRET_S3_SECRET_ACCESS_KEY)

    def _get_or_generate_password(self) -> str:
        existing = self._read_existing_password()
        if existing is not None:
            log.info(f"Reusing existing {_NAMESPACE}/{_SECRET_NAME} password.")
            return existing
        log.info(f"Generating new {_NAMESPACE}/{_SECRET_NAME} password.")
        return secrets.token_urlsafe(24)

    def _read_existing_password(self) -> str | None:
        result = subprocess.run(
            [
                "kubectl",
                "-n",
                _NAMESPACE,
                "get",
                "secret",
                _SECRET_NAME,
                "-o",
                "jsonpath={.data.MINIO_ROOT_PASSWORD}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return base64.b64decode(result.stdout.strip()).decode()

    def _apply_secret(self, password: str) -> None:
        manifest = textwrap.dedent(f"""\
            apiVersion: v1
            kind: Namespace
            metadata:
              name: {_NAMESPACE}
            ---
            apiVersion: v1
            kind: Secret
            metadata:
              name: {_SECRET_NAME}
              namespace: {_NAMESPACE}
            type: Opaque
            stringData:
              MINIO_ROOT_USER: "{_USER}"
              MINIO_ROOT_PASSWORD: "{password}"
        """)
        util.kubectl("apply", "-f", "-", input=manifest, capture=False)

    def _publish(self, node_ip: str, password: str) -> None:
        log.info("Publishing S3_* keys to SM...")
        set_secret(_SECRET_S3_ENDPOINT_URL, util.minio_s3_endpoint_for(node_ip))
        set_secret(_SECRET_S3_REGION, _REGION)
        set_secret(_SECRET_S3_BUCKET_NAME, _BUCKET)
        set_secret(_SECRET_S3_ACCESS_KEY_ID, _USER)
        set_secret(_SECRET_S3_SECRET_ACCESS_KEY, password)
