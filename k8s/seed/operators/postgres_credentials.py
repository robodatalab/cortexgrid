"""PostgresCredentials -- on-prem only.

Mirrors what terraform/platform/rds does for AWS: generate a random master
password, persist it (here: in the in-cluster postgres-credentials Secret;
on AWS: in tfstate via random_password.master), and publish the composed
MLFLOW_BACKEND_STORE_URI and NOTES_DB_URI to AWS Secrets Manager. The
password itself is never stored in SM as a standalone key -- it only
appears inside the two URIs and inside the cluster Secret postgres needs
to bootstrap.

Idempotent: on rerun, reads the existing password from the cluster Secret
rather than regenerating, so postgres data is preserved.

Composed URIs use the head's tailscale IP + NodePort so the same value
works for in-cluster pods (flannel binds to tailscale0) and for laptops
on the tailnet -- matching the AWS posture where the RDS hostname
resolves via the subnet router from anywhere on the tailnet.

Required deps (setup): profile, node_ip.
Required deps (teardown): profile.
"""

import base64
import logging
import secrets
import subprocess
import textwrap

from cortexgrid.secrets import delete_secret, set_secret
from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.postgres_credentials")


_NAMESPACE = "cortexgrid"
_SECRET_NAME = "postgres-credentials"
_USER = "robolab"
_DB_INITIAL = "mlflow"
_DB_NOTES = "notes"
_SECRET_MLFLOW_BACKEND_STORE_URI = "MLFLOW_BACKEND_STORE_URI"
_SECRET_NOTES_DB_URI = "NOTES_DB_URI"


class PostgresCredentials(Operator):
    def setup(self, deps: dict) -> None:
        password = self._get_or_generate_password()
        self._apply_secret(password)
        self._publish_uris(deps["node_ip"], password)

    def teardown(self, deps: dict) -> None:
        delete_secret(_SECRET_MLFLOW_BACKEND_STORE_URI)
        delete_secret(_SECRET_NOTES_DB_URI)

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
                "jsonpath={.data.POSTGRES_PASSWORD}",
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
              POSTGRES_USER: "{_USER}"
              POSTGRES_PASSWORD: "{password}"
              POSTGRES_DB: "{_DB_INITIAL}"
        """)
        util.kubectl("apply", "-f", "-", input=manifest, capture=False)

    def _publish_uris(self, node_ip: str, password: str) -> None:
        log.info("Publishing MLFLOW_BACKEND_STORE_URI + NOTES_DB_URI to SM...")
        set_secret(
            _SECRET_MLFLOW_BACKEND_STORE_URI,
            util.postgres_uri_for(node_ip, _DB_INITIAL, _USER, password),
        )
        set_secret(
            _SECRET_NOTES_DB_URI,
            util.postgres_uri_for(node_ip, _DB_NOTES, _USER, password),
        )
