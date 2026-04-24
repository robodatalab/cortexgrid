"""EnvSecrets — publishes every .env entry to AWS Secrets Manager, one per key."""

import logging

from botocore.exceptions import ClientError  # type: ignore
from dotenv import dotenv_values

from cortexflow.secrets import delete_secret, set_secret
from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


log = logging.getLogger("k8s.seed.operators.env_secrets")


class EnvSecrets(Operator):
    def setup(self, ctx: Context) -> None:
        log.info("Publishing .env entries to AWS Secrets Manager at robolab/infra/*...")
        for k, v in dotenv_values(util.ENV_FILE).items():
            if v is None:
                continue
            set_secret(k, v)
            log.info(f"  published robolab/infra/{k}")

    def teardown(self, ctx: Context) -> None:
        for key in dotenv_values(util.ENV_FILE).keys():
            try:
                delete_secret(key)
                log.info(f"  deleted AWS SM: robolab/infra/{key}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
