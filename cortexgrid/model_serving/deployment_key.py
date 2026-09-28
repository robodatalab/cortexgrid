from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class DeploymentConfig:
    family: str
    suffix: str
    run_name: str
    num_replicas: int = 1
    config: dict[str, str] = field(default_factory=dict)
    serve_app: type | None = None
    source: Callable[[], str | Path] | None = None

    def __post_init__(self) -> None:
        if self.source is not None and self.serve_app is None:
            raise ValueError(
                f"{self.family}/{self.suffix} has a source but no serve_app to front it"
            )


_CONFIG_FINGERPRINT_LENGTH = 12


@dataclass(frozen=True)
class DeploymentKey:
    family: str
    suffix: str
    run_name: str
    config_fingerprint: str = ""


def deployment_key(
    family: str, suffix: str, run_name: str, config: dict[str, str]
) -> DeploymentKey:
    return DeploymentKey(family, suffix, run_name, _config_fingerprint(config))


def _config_fingerprint(config: dict[str, str]) -> str:
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
