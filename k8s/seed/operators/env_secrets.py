"""EnvSecrets — publishes every .env.head entry to the head secrets store, one per key.

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
        log.info(f"Publishing {env_file.name} entries to the head secrets store...")
        for k, v in dotenv_values(env_file).items():
            if v is None:
                continue
            set_secret(k, v)
            log.info(f"  published {k}")

    def teardown(self, deps: dict) -> None:
        env_file = deps["env_file"]
        log.info(f"Deleting {env_file.name} entries from the head secrets store...")
        for key in dotenv_values(env_file).keys():
            delete_secret(key)
            log.info(f"  deleted {key}")
