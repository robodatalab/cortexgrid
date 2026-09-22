from __future__ import annotations

import time
from typing import Any

from ray.serve.schema import ApplicationStatus

from cortexgrid.infra import get_ray_serve_uri
from cortexgrid.model_serving.application_spec import (
    app_name,
    build_application_spec,
    bundle_fingerprint_in_spec,
    replica_count_in_spec,
    route_prefix,
)
from cortexgrid.model_serving.deployment_key import (
    DeploymentConfig,
    DeploymentKey,
    deployment_key,
)
from cortexgrid.model_serving.deployment_records import (
    DeploymentRecord,
    delete_deployment_record,
    get_deployment_record,
    patch_deployment_record,
    put_deployment_record,
)
from cortexgrid.model_serving.placement import ModelRequirements, vram_tiers
from cortexgrid.model_serving.registry_tags import load_deploy_metadata
from cortexgrid.model_serving.serve_bundle import BundleMetadata
from cortexgrid.model_serving.status import (
    PHASE_PAUSED,
    Deployment,
    deployment_of_record,
    observed,
    replaced_bundle_fingerprint_until_rolled_out,
)
from cortexgrid.ray_util import get_serve_details, put_serve_applications


def _current_application_specs() -> list[dict[str, Any]]:
    """Reconstruct the most recently PUT applications list from GET output.

    Ray stores the originally-deployed `ServeApplicationSchema` for each app
    under `applications[<name>].deployed_app_config`, which is what we need to
    PUT-round-trip. Apps without a `deployed_app_config` (e.g. created via
    `serve.run` in-cluster) are skipped: we can't faithfully reproduce them
    from the read-only view.
    """
    details = get_serve_details()
    specs: list[dict[str, Any]] = []
    for app in details.get("applications", {}).values():
        cfg = app.get("deployed_app_config")
        if cfg is not None:
            specs.append(cfg)
    return specs


class ModelDeployFailed(RuntimeError):
    """A model's Serve app cannot reach RUNNING: the controller reported
    DEPLOY_FAILED, or no app exists for the model. Subclasses RuntimeError, which
    `deploy_model(wait=True)` raised before this type existed."""


class ModelNotDeployed(LookupError):
    pass


_SERVING_POLL_INTERVAL_S = 2.0


def _deadline(timeout: float | None) -> float | None:
    return None if timeout is None else time.monotonic() + timeout


def _past(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def wait_for_model_serving(key: DeploymentKey, timeout: float | None = None) -> None:
    """Block until the model's Serve app is RUNNING.

    Raises ModelDeployFailed on DEPLOY_FAILED, carrying the controller's message,
    and as soon as no app exists for the model: never deployed, undeployed, or
    dropped by a concurrent `deploy_model` (each one GETs the applications list,
    splices its own app in and PUTs the whole list back, so a later PUT can drop
    an app an earlier one added). NOT_STARTED, DEPLOYING, UNHEALTHY and DELETING
    are transient; a DELETING app ends up missing. Exceeding a finite `timeout`
    raises TimeoutError; with `timeout=None` there is no deadline.
    """
    _wait_for_application_running(app_name(key), timeout, _deadline(timeout))


def _wait_for_application_running(
    name: str, timeout: float | None, deadline: float | None
) -> None:
    """`wait_for_model_serving` against a deadline already running. `timeout`
    only labels the TimeoutError."""
    while True:
        app = get_serve_details().get("applications", {}).get(name)
        if app is None:
            raise ModelDeployFailed(f"Serve app {name!r} does not exist")
        status = str(app.get("status", "(missing)"))
        message = str(app.get("message", ""))
        if status == ApplicationStatus.RUNNING.value:
            return
        if status == ApplicationStatus.DEPLOY_FAILED.value:
            raise ModelDeployFailed(f"Serve app {name!r} DEPLOY_FAILED: {message}")
        if _past(deadline):
            raise TimeoutError(
                f"Serve app {name!r} did not reach RUNNING within {timeout}s "
                f"(last status={status!r}, message={message!r})"
            )
        time.sleep(_SERVING_POLL_INTERVAL_S)


def _clear_failed_application(
    key: DeploymentKey, timeout: float | None, deadline: float | None
) -> None:
    """Remove the model's DEPLOY_FAILED Serve app, and wait until it, or an app
    already DELETING, is gone.

    Ray resets a failed deployment only when a deploy arrives after the
    deployment is marked for deletion or its version changes. Re-PUTting an
    identical spec over a failed app, or PUTting it back before the controller's
    next tick has processed an undeploy, leaves the failed deployment in place,
    and the app reports DEPLOY_FAILED again without retrying."""
    name = app_name(key)
    app = get_serve_details().get("applications", {}).get(name)
    if app is None:
        return
    status = app.get("status")
    if status == ApplicationStatus.DEPLOY_FAILED.value:
        undeploy_model(key)
    elif status != ApplicationStatus.DELETING.value:
        return
    while name in get_serve_details().get("applications", {}):
        if _past(deadline):
            raise TimeoutError(
                f"Serve app {name!r} was not removed within {timeout}s"
            )
        time.sleep(_SERVING_POLL_INTERVAL_S)


def _spec_already_deployed(spec: dict[str, Any]) -> bool:
    """True when this exact spec is already the app's target, so PUTting it
    again would only disturb the controller.

    Re-PUTting is not a no-op. While an app's build task is in flight its target
    code version is unset, so Ray cancels that build and starts a new one no
    matter what the config says - and since every PUT re-sends the whole
    applications list, it restarts the in-flight builds of the other apps too.
    Our build task downloads the model bundle and may create a pip virtualenv,
    so a caller redeploying faster than that could keep it from ever finishing.

    A DEPLOY_FAILED or DELETING app never reaches here: `_clear_failed_application`
    has already removed it, and an identical PUT over a failed app is exactly the
    no-op that leaves it failed.
    """
    app = get_serve_details().get("applications", {}).get(spec["name"])
    return app is not None and app.get("deployed_app_config") == spec


# Phases in which a deployment record stands for a live app: one a redeploy
# with the same spec may leave alone. A failed app has to be cleared and
# re-PUT, a deleting one waited out, and a missing one PUT again.
_LIVE_PHASES = ("running", "deploying", "not_started", "unhealthy", PHASE_PAUSED)


def _record_is_current(
    record: DeploymentRecord,
    key: DeploymentKey,
    meta: BundleMetadata,
    requirements: ModelRequirements,
    num_replicas: int,
) -> bool:
    """True when the deployment record shows the model live with exactly the
    spec this deploy would PUT.

    The spec is rebuilt against the GPU tiers the record was deployed with,
    so the check asks neither Ray's state API nor the Serve controller: a
    redeploy that changes nothing costs one lookup. A tier added to or gone
    from the cluster since reaches the spec on the next deploy that changes
    anything else; until then the placement fallback keeps larger new GPUs
    usable (see `_placement_preferences`)."""
    if record["phase"] not in _LIVE_PHASES:
        return False
    spec = build_application_spec(key, meta, requirements, num_replicas, record["tiers"])
    return spec == record["spec"]


def _bundle_fingerprint_replaced_by(
    record: DeploymentRecord | None, meta: BundleMetadata
) -> str:
    if record is None or record["phase"] not in _LIVE_PHASES:
        return ""
    rollout_origin = record["replaced_bundle_fingerprint"] or bundle_fingerprint_in_spec(
        record["spec"]
    )
    return "" if rollout_origin == meta.fingerprint else rollout_origin


def _observation_of_application(
    name: str, replaced_bundle_fingerprint: str
) -> DeploymentRecord:
    observation = observed(get_serve_details().get("applications", {}).get(name, {}))
    observation["replaced_bundle_fingerprint"] = (
        replaced_bundle_fingerprint_until_rolled_out(
            replaced_bundle_fingerprint, observation["phase"]
        )
    )
    return observation


def deploy_model(
    family: str,
    suffix: str,
    run_name: str,
    num_replicas: int = 1,
    wait: bool = False,
    timeout: float | None = 300.0,
    config: DeploymentConfig | None = None,
) -> Deployment:
    """Schedule a Ray Serve app for a previously-saved model and return a
    handle carrying its base URL. The caller (e.g. model-gateway) builds
    whatever client the app's routes need - streaming, long timeouts, custom
    request schemas - against that URL; cortexgrid imposes no traffic contract.

    The serve-app class is pulled from the registry entry's tags `save_model`
    wrote at save time; the caller does not need to hold the class object.
    Each of the `num_replicas` replicas requests the model's `ModelRequirements`
    from Ray, so it is placed only on a node that has them free.

    A DEPLOY_FAILED app left by an earlier attempt is undeployed first, and it,
    or an app still DELETING, is waited out before the new spec is PUT, so the
    deploy starts afresh instead of Ray reusing the failed deployment.

    Re-deploying a model that is already live with exactly this spec skips the
    PUT rather than restating it: see `_spec_already_deployed` for what a
    redundant PUT costs. When the model's deployment record already shows it
    live with this spec, the call returns from the record without asking Ray
    at all (see `_record_is_current`). The call still reports the app's phase,
    and with `wait=True` still blocks until it is RUNNING.

    `config` holds this deployment's own settings. A model gets one deployment
    per distinct config, told apart by the `DeploymentKey` on the returned
    `Deployment`; its replicas read the config, laid over the model's own, with
    `model_config`.

    Records the deployment - its config, the spec it PUT, where the app is
    served, and its phase - with the jobs control plane before waiting.

    With `wait=True`, blocks as `wait_for_model_serving` does until the Serve
    controller reports the app RUNNING. `timeout` (default 300) caps the whole
    call, clearing a failed app included; exceeding it raises TimeoutError, and
    DEPLOY_FAILED raises ModelDeployFailed. With `timeout=None` there is no cap.
    Tradeoff: an app that never reaches a terminal state (e.g. GPU-starved,
    stuck in DEPLOYING) will hang forever.
    """
    deadline = _deadline(timeout)
    deployment_config = config or {}
    key = deployment_key(family, suffix, run_name, deployment_config)
    meta, requirements = load_deploy_metadata(family, suffix, run_name)
    record = get_deployment_record(key)
    if record is not None and _record_is_current(
        record, key, meta, requirements, num_replicas
    ):
        if wait:
            _wait_for_application_running(record["spec"]["name"], timeout, deadline)
            record = {
                **record,
                "phase": observed(
                    get_serve_details().get("applications", {}).get(record["spec"]["name"])
                )["phase"],
            }
        return deployment_of_record(record)
    # Read afresh on every deploy that reaches Ray: the tiers are what the
    # model is placed against, so a GPU joining or leaving the cluster has to
    # change the spec (and therefore re-PUT it).
    replaced_bundle_fingerprint = _bundle_fingerprint_replaced_by(record, meta)
    tiers = vram_tiers()
    spec = build_application_spec(key, meta, requirements, num_replicas, tiers)
    _clear_failed_application(key, timeout, deadline)
    if not _spec_already_deployed(spec):
        existing = [
            a for a in _current_application_specs() if a["name"] != spec["name"]
        ]
        # The controller registers the app, sets it DEPLOYING and stamps
        # last_deployed_time_s before the PUT returns, so the wait below neither
        # misses the app nor reads a status left by an earlier deploy.
        put_serve_applications([*existing, spec])
    url = f"{get_ray_serve_uri()}{route_prefix(key)}"
    observation = _observation_of_application(spec["name"], replaced_bundle_fingerprint)
    put_deployment_record(
        key,
        {
            "config": deployment_config,
            "spec": spec,
            "tiers": tiers,
            "url": url,
            **observation,
        },
    )
    if wait:
        _wait_for_application_running(spec["name"], timeout, deadline)
        observation = _observation_of_application(
            spec["name"], replaced_bundle_fingerprint
        )
        patch_deployment_record(key, observation)
    return Deployment(
        key=key,
        config=deployment_config,
        url=url,
        phase=observation["phase"],
        bundle_fingerprint=meta.fingerprint,
        replaced_bundle_fingerprint=observation["replaced_bundle_fingerprint"],
    )


def redeploy_model(key: DeploymentKey) -> Deployment:
    record = get_deployment_record(key)
    if record is None:
        raise ModelNotDeployed(f"{key} is not deployed")
    return deploy_model(
        key.family,
        key.suffix,
        key.run_name,
        num_replicas=replica_count_in_spec(record["spec"]),
        config=record["config"],
    )


def undeploy_model(key: DeploymentKey) -> None:
    """Tear down the Ray Serve app of this deployment and drop its record."""
    name = app_name(key)
    remaining = [a for a in _current_application_specs() if a["name"] != name]
    put_serve_applications(remaining)
    delete_deployment_record(key)
