"""TerraformOutputs -- AWS profile only.

The AWS analog of MinioCredentials + PostgresCredentials: terraform/platform
provisions S3, RDS and the robolab-dgx IAM user, and this operator publishes
their coordinates and credentials from `terraform output` to the head secrets
store: S3_BUCKET_NAME, S3_ENDPOINT_URL, S3_REGION, S3_ACCESS_KEY_ID,
S3_SECRET_ACCESS_KEY, MLFLOW_BACKEND_STORE_URI, NOTES_DB_URI and
CORTEXGRID_DB_URI.

Needs terraform on the laptop, AWS credentials that can read the
terraform/platform state, and the stack applied (`make head-aws-apply`).

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

# secret id -> terraform/platform output name
_OUTPUTS = {
    "S3_BUCKET_NAME": "s3_bucket_name",
    "S3_ENDPOINT_URL": "s3_endpoint_url",
    "S3_REGION": "s3_region",
    "S3_ACCESS_KEY_ID": "s3_access_key_id",
    "S3_SECRET_ACCESS_KEY": "s3_secret_access_key",
    "MLFLOW_BACKEND_STORE_URI": "mlflow_backend_store_uri",
    "NOTES_DB_URI": "notes_db_uri",
    "CORTEXGRID_DB_URI": "cortexgrid_db_uri",
}


class TerraformOutputs(Operator):
    def setup(self, deps: dict) -> None:
        outputs = _read_outputs(_PLATFORM_DIR)
        missing = sorted(name for name in _OUTPUTS.values() if name not in outputs)
        if missing:
            sys.exit(
                f"Error: terraform/platform outputs missing: {', '.join(missing)}. "
                f"Apply the stack first (make head-aws-apply)."
            )
        log.info("Publishing terraform outputs to the head secrets store...")
        for secret_id, name in _OUTPUTS.items():
            set_secret(secret_id, outputs[name])

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
