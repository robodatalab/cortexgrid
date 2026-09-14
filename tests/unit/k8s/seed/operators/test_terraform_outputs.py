from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from k8s.seed.operators import terraform_outputs
from k8s.seed.operators.terraform_outputs import TerraformOutputs


_PLATFORM = {
    "s3_bucket_name": "bucket",
    "s3_endpoint_url": "https://s3.eu-west-2.amazonaws.com",
    "s3_region": "eu-west-2",
    "mlflow_backend_store_uri": "postgresql://mlflow",
    "notes_db_uri": "postgresql://notes",
    "rds_endpoint": "unused",
}
_SECRETS = {
    "dgx_user_access_key_id": "AKIA",
    "dgx_user_secret_access_key": "shh",
}


def _terraform(outputs_by_dir: dict, returncode: int = 0):
    def run(cmd, **_kwargs):
        tf_dir = cmd[1].removeprefix("-chdir=")
        outputs = outputs_by_dir[tf_dir]
        stdout = json.dumps({k: {"value": v, "sensitive": False} for k, v in outputs.items()})
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="boom")

    return run


class TestTerraformOutputs(unittest.TestCase):
    """AWS profile: publishes S3, RDS and dgx-user outputs to the head secrets store."""

    def _setup(self, outputs_by_dir: dict, returncode: int = 0) -> mock.MagicMock:
        with (
            mock.patch.object(
                terraform_outputs.subprocess, "run", _terraform(outputs_by_dir, returncode)
            ),
            mock.patch.object(terraform_outputs, "set_secret") as set_secret,
        ):
            TerraformOutputs().setup({})
        return set_secret

    def test_publishes_every_output(self) -> None:
        set_secret = self._setup(
            {
                str(terraform_outputs._PLATFORM_DIR): _PLATFORM,
                str(terraform_outputs._SECRETS_DIR): _SECRETS,
            }
        )
        self.assertEqual(
            dict(c.args for c in set_secret.call_args_list),
            {
                "S3_BUCKET_NAME": "bucket",
                "S3_ENDPOINT_URL": "https://s3.eu-west-2.amazonaws.com",
                "S3_REGION": "eu-west-2",
                "MLFLOW_BACKEND_STORE_URI": "postgresql://mlflow",
                "NOTES_DB_URI": "postgresql://notes",
                "S3_ACCESS_KEY_ID": "AKIA",
                "S3_SECRET_ACCESS_KEY": "shh",
                "ROUTE53_ACCESS_KEY_ID": "AKIA",
                "ROUTE53_SECRET_ACCESS_KEY": "shh",
            },
        )

    def test_missing_outputs_exit_before_publishing(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._setup(
                {
                    str(terraform_outputs._PLATFORM_DIR): _PLATFORM,
                    str(terraform_outputs._SECRETS_DIR): {},
                }
            )
        self.assertIn("terraform/platform/secrets:dgx_user_access_key_id", str(ctx.exception))

    def test_terraform_failure_exits(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._setup(
                {
                    str(terraform_outputs._PLATFORM_DIR): {},
                    str(terraform_outputs._SECRETS_DIR): {},
                },
                returncode=1,
            )
        self.assertIn("terraform init", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
