"""PlatformConfig - mirrors the .env real-AWS keys into per-purpose SM slots.

The cluster splits AWS API access into three named identities, each with its
own SM key prefix and (later) its own env-var namespace:

  SM_*       - AWS Secrets Manager access (real AWS on both profiles)
  ROUTE53_*  - cert-manager Route53 access (real AWS on both profiles)
  S3_*       - object storage (real AWS on AWS profile, MinIO on on-prem)

This operator runs after EnvSecrets has populated `robolab/infra/SM_*` from
.env and mirrors that real-AWS value into the ROUTE53_* slot on both profiles,
plus the S3_* slot on the AWS profile only (on on-prem S3_* are MinIO admin
and come from MinioCredentials). The mirror means a user can keep just
SM_ACCESS_KEY_ID/SECRET in .env and still have all three identities
populated; replacing any SM key value later (e.g. rotating the Route53 user
separately) does not require re-running this operator.

Required deps (setup): profile, sm_access_key_id, sm_secret_access_key.
"""

import logging

from cortexgrid.secrets import delete_secret, set_secret
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.platform_config")


_MIRROR_TARGETS_ALL_PROFILES = [
    ("ROUTE53_ACCESS_KEY_ID", "ROUTE53_SECRET_ACCESS_KEY"),
]
_MIRROR_TARGETS_AWS_PROFILE_ONLY = [
    ("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"),
]


class PlatformConfig(Operator):
    def setup(self, deps: dict) -> None:
        access_key = deps["sm_access_key_id"]
        secret_key = deps["sm_secret_access_key"]
        targets = list(_MIRROR_TARGETS_ALL_PROFILES)
        if deps["profile"] == "aws":
            targets.extend(_MIRROR_TARGETS_AWS_PROFILE_ONLY)
        log.info(
            "Mirroring SM creds to %d additional identity slot(s)...", len(targets)
        )
        for access_key_name, secret_key_name in targets:
            set_secret(access_key_name, access_key)
            set_secret(secret_key_name, secret_key)

    def teardown(self, deps: dict) -> None:
        # Idempotent. On on-prem the S3_* keys may have been written by
        # MinioCredentials rather than this operator, but deleting on teardown
        # is still appropriate -- the cluster is going away.
        targets = (
            _MIRROR_TARGETS_ALL_PROFILES + _MIRROR_TARGETS_AWS_PROFILE_ONLY
        )
        for access_key_name, secret_key_name in targets:
            delete_secret(access_key_name)
            delete_secret(secret_key_name)
