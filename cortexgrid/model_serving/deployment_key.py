from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


DeploymentConfig = dict[str, str]

_CONFIG_FINGERPRINT_LENGTH = 12


@dataclass(frozen=True)
class DeploymentKey:
    family: str
    suffix: str
    run_name: str
    config_fingerprint: str = ""


def deployment_key(
    family: str, suffix: str, run_name: str, config: DeploymentConfig
) -> DeploymentKey:
    return DeploymentKey(family, suffix, run_name, _config_fingerprint(config))


def _config_fingerprint(config: DeploymentConfig) -> str:
    for name, value in config.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError(
                f"Deployment config must map strings to strings: {name!r}: {value!r}"
            )
    if not config:
        return ""
    canonical_config = json.dumps(config, sort_keys=True)
    return hashlib.sha256(canonical_config.encode()).hexdigest()[
        :_CONFIG_FINGERPRINT_LENGTH
    ]
