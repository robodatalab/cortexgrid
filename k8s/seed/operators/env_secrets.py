"""EnvSecrets — publishes every .env entry to AWS Secrets Manager, one per key.

Required deps: env_file.
"""

import logging

from dotenv import dotenv_values

from cortexgrid.secrets import delete_secret, set_secret
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.env_secrets")


class EnvSecrets(Operator):
    def setup(self, deps: dict) -> None:
        env_file = deps["env_file"]
        log.info("Publishing .env entries to AWS Secrets Manager at robolab/infra/*...")
        for k, v in dotenv_values(env_file).items():
            if v is None:
                continue
            set_secret(k, v)
            log.info(f"  published robolab/infra/{k}")

    def teardown(self, deps: dict) -> None:
        env_file = deps["env_file"]
        log.info("Deleting .env entries from AWS Secrets Manager at robolab/infra/*...")
        for key in dotenv_values(env_file).keys():
            delete_secret(key)
            log.info(f"  deleted robolab/infra/{key}")
