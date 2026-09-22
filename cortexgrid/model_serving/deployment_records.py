from __future__ import annotations

from typing import Any

from cortexgrid import state
from cortexgrid.model_serving.deployment_key import DeploymentKey


DeploymentRecord = dict[str, Any]


def get_deployment_record(key: DeploymentKey) -> DeploymentRecord | None:
    return state.get(*_path(key), params=_params(key))


def put_deployment_record(key: DeploymentKey, body: DeploymentRecord) -> None:
    state.put(*_path(key), body=body, params=_params(key))


def patch_deployment_record(key: DeploymentKey, body: DeploymentRecord) -> bool:
    return state.patch(*_path(key), body=body, params=_params(key))


def delete_deployment_record(key: DeploymentKey) -> None:
    state.delete(*_path(key), params=_params(key))


def list_deployment_records() -> list[DeploymentRecord]:
    return state.get("deployments")


def key_of_record(record: DeploymentRecord) -> DeploymentKey:
    return DeploymentKey(
        record["family"],
        record["suffix"],
        record["run_name"],
        record["config_fingerprint"],
    )


def _path(key: DeploymentKey) -> tuple[str, ...]:
    return ("deployments", key.family, key.suffix, key.run_name)


def _params(key: DeploymentKey) -> dict[str, str]:
    return {"config_fingerprint": key.config_fingerprint}
