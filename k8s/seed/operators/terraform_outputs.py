"""TerraformOutputs -- AWS profile only.

The AWS analog of MinioCredentials + PostgresCredentials: terraform provisions
S3, RDS and the dgx IAM user, and this operator publishes their coordinates and
credentials from `terraform output` to the head secrets store.

  terraform/platform:          S3_BUCKET_NAME, S3_ENDPOINT_URL, S3_REGION,
                               MLFLOW_BACKEND_STORE_URI, NOTES_DB_URI
  terraform/platform/secrets:  the dgx IAM user's access key, published as both
                               S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY and
                               ROUTE53_ACCESS_KEY_ID / ROUTE53_SECRET_ACCESS_KEY

Needs terraform on the laptop, AWS credentials that can read both states, and
both stacks applied (`make core-aws-setup`, `make head-aws-apply`).

Required deps: (none).
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

from cortexgrid.secrets import delete_secret, set_secret
from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.terraform_outputs")


_PLATFORM_DIR = util.REPO_ROOT / "terraform" / "platform"
_SECRETS_DIR = _PLATFORM_DIR / "secrets"

# secret id -> (terraform root, output name)
_OUTPUTS = {
    "S3_BUCKET_NAME": (_PLATFORM_DIR, "s3_bucket_name"),
    "S3_ENDPOINT_URL": (_PLATFORM_DIR, "s3_endpoint_url"),
    "S3_REGION": (_PLATFORM_DIR, "s3_region"),
    "MLFLOW_BACKEND_STORE_URI": (_PLATFORM_DIR, "mlflow_backend_store_uri"),
    "NOTES_DB_URI": (_PLATFORM_DIR, "notes_db_uri"),
    "S3_ACCESS_KEY_ID": (_SECRETS_DIR, "dgx_user_access_key_id"),
    "S3_SECRET_ACCESS_KEY": (_SECRETS_DIR, "dgx_user_secret_access_key"),
    "ROUTE53_ACCESS_KEY_ID": (_SECRETS_DIR, "dgx_user_access_key_id"),
    "ROUTE53_SECRET_ACCESS_KEY": (_SECRETS_DIR, "dgx_user_secret_access_key"),
}


class TerraformOutputs(Operator):
    def setup(self, deps: dict) -> None:
        outputs = {tf_dir: _read_outputs(tf_dir) for tf_dir in (_PLATFORM_DIR, _SECRETS_DIR)}
        missing = sorted(
            f"{tf_dir.relative_to(util.REPO_ROOT)}:{name}"
            for tf_dir, name in _OUTPUTS.values()
            if name not in outputs[tf_dir]
        )
        if missing:
            sys.exit(
                f"Error: terraform outputs missing: {', '.join(missing)}. "
                f"Apply both stacks first (make core-aws-setup, make head-aws-apply)."
            )
        log.info("Publishing terraform outputs to the head secrets store...")
        for secret_id, (tf_dir, name) in _OUTPUTS.items():
            set_secret(secret_id, outputs[tf_dir][name])

    def teardown(self, deps: dict) -> None:
        for secret_id in _OUTPUTS:
            delete_secret(secret_id)


def _read_outputs(tf_dir: Path) -> dict[str, str]:
    result = subprocess.run(
        ["terraform", f"-chdir={tf_dir}", "output", "-json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(
            f"Error: `terraform output` failed in {tf_dir.relative_to(util.REPO_ROOT)}. "
            f"Run `terraform init` there with AWS credentials that can read its state.\n"
            f"{result.stderr}"
        )
    return {name: output["value"] for name, output in json.loads(result.stdout).items()}
