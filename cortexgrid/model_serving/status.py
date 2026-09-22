from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from ray.serve.schema import ApplicationStatus, ReplicaState

from cortexgrid.model_serving.application_spec import (
    app_name,
    bundle_fingerprint_in_spec,
)
from cortexgrid.model_serving.deployment_key import DeploymentConfig, DeploymentKey
from cortexgrid.model_serving.deployment_records import (
    DeploymentRecord,
    get_deployment_record,
    key_of_record,
    list_deployment_records,
    patch_deployment_record,
)
from cortexgrid.ray_util import get_serve_details


_PHASE_NOT_DEPLOYED = "not_deployed"
PHASE_PAUSED = "paused"
_ROLLED_OUT_PHASES = ("running", PHASE_PAUSED)

# Ray Serve ApplicationStatus -> normalized serving phase. The single source of
# the serving vocabulary, shared by Deployment, list_deployed_models, and
# model_serving_status.
_PHASE_BY_SERVE_STATUS = {
    ApplicationStatus.RUNNING.value: "running",
    ApplicationStatus.DEPLOYING.value: "deploying",
    ApplicationStatus.NOT_STARTED.value: "not_started",
    ApplicationStatus.UNHEALTHY.value: "unhealthy",
    ApplicationStatus.DEPLOY_FAILED.value: "failed",
    ApplicationStatus.DELETING.value: "deleting",
}


def _serve_phase(raw_status: str) -> str:
    """Map a Ray Serve app status to the normalized serving phase, defaulting to
    "deploying" for an app that exists but has no recognized status yet."""
    return _PHASE_BY_SERVE_STATUS.get(raw_status, "deploying")


@dataclass
class Deployment:
    """A scheduled Ray Serve app fronting a model. `phase` is the normalized
    serving lifecycle phase (see `ServingStatus`); an app that appears in a
    listing always exists, so its phase is never "not_deployed"."""

    key: DeploymentKey
    config: DeploymentConfig
    url: str
    phase: str
    bundle_fingerprint: str
    replaced_bundle_fingerprint: str


def replaced_bundle_fingerprint_until_rolled_out(
    replaced_bundle_fingerprint: str, phase: str
) -> str:
    return "" if phase in _ROLLED_OUT_PHASES else replaced_bundle_fingerprint


def list_deployed_models() -> list[Deployment]:
    """Return a Deployment for every model `deploy_model` put on Ray Serve
    whose app the control plane last saw existing."""
    return [
        deployment_of_record(record)
        for record in list_deployment_records()
        if record["phase"] != _PHASE_NOT_DEPLOYED
    ]


def deployment_of_record(record: DeploymentRecord) -> Deployment:
    return Deployment(
        key=key_of_record(record),
        config=record["config"],
        url=record["url"],
        phase=record["phase"],
        bundle_fingerprint=bundle_fingerprint_in_spec(record["spec"]),
        replaced_bundle_fingerprint=record["replaced_bundle_fingerprint"],
    )


def deployment_config(key: DeploymentKey) -> DeploymentConfig:
    record = get_deployment_record(key)
    if record is None:
        raise ValueError(f"No deployment {key}")
    return record["config"]


@dataclass
class ServingStatus:
    """Serving lifecycle of one model, owned by the Ray Serve controller.

    This is the serving half of a model's life. The registry half (uploading /
    ready in the registry) is a separate lifecycle reported by
    `cortexgrid.model_storage.model_registry_status`.

    `phase` is one of:
      - "not_deployed"  no Serve app: never deployed, or already undeployed
      - "not_started"   controller accepted the app but has not started it yet
                        (NOT_STARTED)
      - "deploying"     replicas starting; the replica pulls the weights and
                        builds the model on the worker (DEPLOYING)
      - "running"       serving traffic (RUNNING)
      - "paused"        scaled to zero replicas by the model scheduler
      - "unhealthy"     Serve app reports UNHEALTHY
      - "failed"        Serve app DEPLOY_FAILED
      - "deleting"      Serve app being torn down (DELETING)
    """

    key: DeploymentKey
    phase: str
    message: str
    url: str | None


def model_serving_status(key: DeploymentKey) -> ServingStatus:
    """Report the serving lifecycle phase of a model from its deployment
    record, as the control plane last observed it on the Ray Serve controller.

    The serving lifecycle begins when `deploy_model` schedules the app and ends
    when `undeploy_model` tears it down; outside that window the phase is
    "not_deployed". While an app exists the phase reflects the controller's
    status ("deploying" while the replica pulls weights and builds the model on
    the worker, then "running"). See `ServingStatus` for the full vocabulary.
    The registry lifecycle is reported separately by
    `cortexgrid.model_storage.model_registry_status`.
    """
    record = get_deployment_record(key)
    if record is None or record["phase"] == _PHASE_NOT_DEPLOYED:
        return ServingStatus(key, _PHASE_NOT_DEPLOYED, "", None)
    return ServingStatus(
        key=key,
        phase=record["phase"],
        message=record["message"],
        url=record["url"],
    )


@dataclass
class ReplicaPlacement:
    """Where one replica of a model's Serve app ended up.

    `node_ip` is the address of the Ray worker running it, which on the
    cluster is the ray-worker pod's IP - the dashboard joins on it to name the
    machine and report its health. It is None for a replica the controller has
    accepted but not yet placed."""

    replica_id: str
    state: str
    node_id: str | None
    node_ip: str | None


def model_replica_placements(key: DeploymentKey) -> list[ReplicaPlacement]:
    """Report which worker each of a model's replicas is running on.

    This is what the requirements and the size-class preferences actually
    resolved to: `deploy_model` asks for the smallest GPU that fits, and this
    is the card it got. Read from the deployment record, as the control plane
    last observed it. Empty when no app exists, and while an app is
    `deploying` it fills in as replicas are placed.
    """
    record = get_deployment_record(key)
    if record is None:
        return []
    return [ReplicaPlacement(**replica) for replica in record["replicas"]]


def _placements(app: dict[str, Any]) -> list[ReplicaPlacement]:
    """Where the Serve controller reports each replica of an app running."""
    return [
        ReplicaPlacement(
            replica_id=str(replica.get("replica_id", "")),
            state=str(replica.get("state", "")),
            node_id=replica.get("node_id"),
            node_ip=replica.get("node_ip"),
        )
        for deployment in app.get("deployments", {}).values()
        for replica in deployment.get("replicas", [])
    ]


def observed(app: dict[str, Any] | None) -> dict[str, Any]:
    """What the Serve controller reports for one app, in the fields a
    deployment record keeps; an app that does not exist reads as
    "not_deployed"."""
    if app is None:
        return {"phase": _PHASE_NOT_DEPLOYED, "message": "", "replicas": []}
    raw = str(app.get("status", ""))
    return {
        "phase": _phase(app),
        "message": str(app.get("message", "")) or raw,
        "replicas": [asdict(placement) for placement in _placements(app)],
    }


def _phase(app: dict[str, Any]) -> str:
    serve_phase = _serve_phase(str(app.get("status", "")))
    if serve_phase != "running":
        return serve_phase
    deployments = list(app.get("deployments", {}).values())
    target_replica_counts = [
        deployment.get("target_num_replicas") for deployment in deployments
    ]
    if target_replica_counts and all(count == 0 for count in target_replica_counts):
        return PHASE_PAUSED
    if any(target_replica_counts) and not _has_running_replica(deployments):
        return "deploying"
    return serve_phase


def _has_running_replica(deployments: list[dict[str, Any]]) -> bool:
    return any(
        replica.get("state") == ReplicaState.RUNNING.value
        for deployment in deployments
        for replica in deployment.get("replicas", [])
    )


def observe_deployments() -> None:
    """Bring every deployment record up to date with one read of the Serve
    controller: its phase, message, and where its replicas run. The jobs
    control plane calls this on every poll cycle; only records whose
    observation changed are written."""
    applications = get_serve_details().get("applications", {})
    for record in list_deployment_records():
        key = key_of_record(record)
        observation = observed(applications.get(app_name(key)))
        observation["replaced_bundle_fingerprint"] = (
            replaced_bundle_fingerprint_until_rolled_out(
                record["replaced_bundle_fingerprint"], observation["phase"]
            )
        )
        if any(record[field] != value for field, value in observation.items()):
            patch_deployment_record(key, observation)


@dataclass
class ServingMessage:
    """One message the Ray Serve controller reports for a model's app. `source`
    is "application" for the app-level message (e.g. the app failed to build)
    or a deployment name for that deployment's message (e.g. its replicas
    failed to start); `status` is the raw Ray Serve status of that source."""

    source: str
    status: str
    message: str


# Ray colors parts of its messages (e.g. the serialization checker's "!!! FAIL")
# with ANSI escapes, which are noise outside a terminal.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def model_serving_messages(key: DeploymentKey) -> list[ServingMessage]:
    """Return the non-empty controller messages for a model's Serve app: the
    app-level message first, then each deployment's. Empty when no app exists.
    This is where Ray explains a DEPLOY_FAILED or UNHEALTHY app."""
    app = get_serve_details().get("applications", {}).get(app_name(key))
    if app is None:
        return []
    sources = [("application", app)] + list(app.get("deployments", {}).items())
    return [
        ServingMessage(
            source=source,
            status=str(details.get("status", "")),
            message=_ANSI_ESCAPE.sub("", str(details["message"])),
        )
        for source, details in sources
        if details.get("message")
    ]
