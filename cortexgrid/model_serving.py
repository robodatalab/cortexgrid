"""Cortexgrid wrappers around Ray Serve.

Caller stays HTTP-only: deploy/undeploy/list talk to the Ray dashboard's
declarative `/api/serve/applications/` endpoint via [cortexgrid.ray_util],
never `ray.init`. The deployment class is bundled at `save_model` time, zipped,
uploaded to MinIO under `serve-bundles/<run_name>/<family>__<suffix>.zip`, and
referenced via `runtime_env.working_dir` so Ray workers fetch it from there.
The bundle URL, class import path, and pip list are persisted as MLflow tags
on the ModelVersion so `deploy_model` can find them later without the caller
holding the class object.

Naming: the Ray Serve application is named "<family>__<suffix>__<run_name>".
This relies on family/suffix/run_name not containing the literal "__".

See [docs/cortexgrid/model-serving.md](../docs/cortexgrid/model-serving.md) for
the end-to-end design.
"""

from __future__ import annotations

import inspect
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mlflow.tracking import MlflowClient
from ray.serve.schema import ApplicationStatus

from cortexgrid._bundle import bundle, stage, worker_provides
from cortexgrid.infra import get_mlflow_tracking_uri, get_ray_serve_uri
from cortexgrid.ray_util import (
    get_serve_details,
    put_serve_applications,
)
from cortexgrid.s3_util import upload


log = logging.getLogger(__name__)


@dataclass
class Deployment:
    """A scheduled Ray Serve app fronting a model. `phase` is the normalized
    serving lifecycle phase (see `ServingStatus`); an app that appears in a
    listing always exists, so its phase is never "not_deployed"."""

    family: str
    suffix: str
    run_name: str
    url: str
    phase: str


def _app_name(family: str, suffix: str, run_name: str) -> str:
    return f"{family}__{suffix}__{run_name}"


def _route_prefix(family: str, suffix: str, run_name: str) -> str:
    return f"/r/{family}/{suffix}/{run_name}"


_PHASE_NOT_DEPLOYED = "not_deployed"

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
class BundleMetadata:
    """What `bundle_class` produces and `deploy_model` needs to PUT the app."""

    bundle_url: str
    class_import_path: str
    # pinned third-party requirements the replica pip-installs (the bundle's
    # distributions the Ray image does not already provide)
    pip_requirements: list[str] = field(default_factory=list)


def bundle_class(
    cls: type, family: str, suffix: str, run_name: str
) -> BundleMetadata:
    """Bundle the serve-app class's code (and the serve entrypoint), zip it, and
    upload to MinIO.

    Returns the metadata `deploy_model` needs later; callers (typically
    `save_model`) persist it on the ModelVersion so the deploy step can run
    without holding the class object."""
    entry_file = Path(inspect.getfile(cls)).resolve()
    serve_entry = Path(__file__).with_name("_serve_entry.py")
    desc = bundle(entry_file).merge(bundle(serve_entry))
    pip_requirements = desc.pip_requirements(worker_provides())
    with tempfile.TemporaryDirectory() as tmp:
        code_root = Path(tmp) / "code"
        stage(desc.local_files, code_root)
        log.info(
            "Serve bundle for %s/%s/%s: %d files, pip: %s",
            family,
            suffix,
            run_name,
            len(desc.local_files),
            pip_requirements,
        )
        zip_base = Path(tmp) / f"{family}__{suffix}"
        shutil.make_archive(str(zip_base), "zip", root_dir=str(code_root))
        bundle_url = upload(
            str(zip_base.with_suffix(".zip")),
            dest_path=f"serve-bundles/{run_name}/{family}__{suffix}.zip",
        )
    return BundleMetadata(
        bundle_url=bundle_url,
        class_import_path=f"{cls.__module__}:{cls.__name__}",
        pip_requirements=pip_requirements,
    )


def _build_application_spec(
    family: str, suffix: str, run_name: str, meta: BundleMetadata
) -> dict[str, Any]:
    """Assemble a Ray Serve application schema from pre-bundled metadata."""
    # working_dir carries the serve-app's own source; Ray pip-installs the
    # third-party distributions the image lacks into a per-node cached
    # virtualenv layered on the image. No pip key when there are none, so Ray
    # builds no virtualenv.
    runtime_env: dict[str, Any] = {"working_dir": meta.bundle_url}
    if meta.pip_requirements:
        runtime_env["pip"] = meta.pip_requirements
    return {
        "name": _app_name(family, suffix, run_name),
        "route_prefix": _route_prefix(family, suffix, run_name),
        # Ray Serve REST requires import_path to point at an Application builder
        # (callable returning a bound node) or an already-bound node. A bare
        # Deployment class is rejected, so cortexgrid.deploy_model goes through
        # a generic builder that re-imports the user's class and binds it.
        "import_path": "cortexgrid._serve_entry:build",
        "args": {
            "class_import_path": meta.class_import_path,
            "family": family,
            "suffix": suffix,
            "run_name": run_name,
        },
        "runtime_env": runtime_env,
    }


# MLflow tag keys for the bundle metadata `save_model` writes and
# `deploy_model` reads back.
_CLASS_IMPORT_PATH_TAG = "class_import_path"
_BUNDLE_URL_TAG = "serve_bundle_url"
_PIP_REQUIREMENTS_TAG = "serve_pip_requirements"


def metadata_to_tags(meta: BundleMetadata) -> dict[str, str]:
    """Serialise BundleMetadata to MLflow tags. The inverse of
    `_load_bundle_metadata`; lives here next to the consumer so the tag schema
    stays in one place."""
    return {
        _CLASS_IMPORT_PATH_TAG: meta.class_import_path,
        _BUNDLE_URL_TAG: meta.bundle_url,
        _PIP_REQUIREMENTS_TAG: json.dumps(meta.pip_requirements),
    }


def _load_bundle_metadata(
    family: str, suffix: str, run_name: str
) -> BundleMetadata:
    """Read the bundle metadata `save_model` persisted on the ModelVersion."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    name = f"{family}__{suffix}"
    versions = client.search_model_versions(
        f"name='{name}' and tags.run_name='{run_name}'"
    )
    if not versions:
        raise ValueError(
            f"No saved model for {family}/{suffix}/{run_name}; cannot deploy."
        )
    tags = versions[0].tags or {}
    try:
        return BundleMetadata(
            bundle_url=tags[_BUNDLE_URL_TAG],
            class_import_path=tags[_CLASS_IMPORT_PATH_TAG],
            # Absent on models saved before dependencies were pip-installed.
            pip_requirements=json.loads(tags.get(_PIP_REQUIREMENTS_TAG, "[]")),
        )
    except KeyError as exc:
        raise ValueError(
            f"Saved model {family}/{suffix}/{run_name} is missing the deployment "
            f"bundle tag {exc.args[0]!r}; re-save with `cortexgrid.save_model`."
        ) from exc


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


def _wait_for_application_running(
    name: str, timeout_s: float | None = 300.0, interval_s: float = 2.0
) -> None:
    """Poll the Serve controller until the named application is RUNNING.

    Raises immediately on DEPLOY_FAILED with the controller's message. Other
    non-RUNNING statuses (NOT_STARTED, DEPLOYING, UNHEALTHY) are treated as
    transient until the timeout fires. With `timeout_s=None` there is no
    deadline: the loop blocks until a terminal status (RUNNING or
    DEPLOY_FAILED) is reached.
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    last_status: str = "(missing)"
    last_message: str = ""
    while deadline is None or time.monotonic() < deadline:
        app = get_serve_details().get("applications", {}).get(name)
        if app is not None:
            last_status = str(app.get("status", "(missing)"))
            last_message = str(app.get("message", ""))
            if last_status == ApplicationStatus.RUNNING.value:
                return
            if last_status == ApplicationStatus.DEPLOY_FAILED.value:
                raise RuntimeError(
                    f"Serve app {name!r} DEPLOY_FAILED: {last_message}"
                )
        time.sleep(interval_s)
    raise TimeoutError(
        f"Serve app {name!r} did not reach RUNNING within {timeout_s}s "
        f"(last status={last_status!r}, message={last_message!r})"
    )


def deploy_model(
    family: str,
    suffix: str,
    run_name: str,
    wait: bool = False,
    timeout: float | None = 300.0,
) -> Deployment:
    """Schedule a Ray Serve app for a previously-saved model and return a
    handle carrying its base URL. The caller (e.g. model-gateway) builds
    whatever client the app's routes need - streaming, long timeouts, custom
    request schemas - against that URL; cortexgrid imposes no traffic contract.

    The serve-app class is pulled from the MLflow ModelVersion tags `save_model`
    wrote at save time; the caller does not need to hold the class object.

    With `wait=True`, blocks until the Serve controller reports the app
    RUNNING, capped at `timeout` seconds (default 300). DEPLOY_FAILED raises;
    exceeding a finite `timeout` raises TimeoutError. With `timeout=None` the
    wait is unbounded: it blocks until a terminal status (RUNNING or
    DEPLOY_FAILED) is reached. Tradeoff: an app that never reaches a terminal
    state (e.g. GPU-starved, stuck in DEPLOYING) will hang forever.
    """
    meta = _load_bundle_metadata(family, suffix, run_name)
    spec = _build_application_spec(family, suffix, run_name, meta)
    existing = [a for a in _current_application_specs() if a["name"] != spec["name"]]
    put_serve_applications([*existing, spec])
    if wait:
        _wait_for_application_running(spec["name"], timeout_s=timeout)
    app = get_serve_details().get("applications", {}).get(spec["name"], {})
    return Deployment(
        family=family,
        suffix=suffix,
        run_name=run_name,
        url=f"{get_ray_serve_uri()}{_route_prefix(family, suffix, run_name)}",
        phase=_serve_phase(str(app.get("status", ""))),
    )


def undeploy_model(family: str, suffix: str, run_name: str) -> None:
    """Tear down the Ray Serve app for this model."""
    name = _app_name(family, suffix, run_name)
    remaining = [a for a in _current_application_specs() if a["name"] != name]
    put_serve_applications(remaining)


def list_deployed_models() -> list[Deployment]:
    """Return Deployment records for every Ray Serve app whose name matches our scheme."""
    base = get_ray_serve_uri()
    details = get_serve_details()
    result: list[Deployment] = []
    for app_name, app in details.get("applications", {}).items():
        parts = app_name.split("__")
        if len(parts) != 3:
            continue
        family, suffix, run_name = parts
        result.append(
            Deployment(
                family=family,
                suffix=suffix,
                run_name=run_name,
                url=f"{base}{_route_prefix(family, suffix, run_name)}",
                phase=_serve_phase(str(app.get("status", ""))),
            )
        )
    return result


@dataclass
class ServingStatus:
    """Serving lifecycle of one model, owned by the Ray Serve controller.

    This is the serving half of a model's life. The registry half (uploading /
    ready in MLflow) is a separate lifecycle reported by
    `cortexgrid.model_storage.model_registry_status`.

    `phase` is one of:
      - "not_deployed"  no Serve app: never deployed, or already undeployed
      - "not_started"   controller accepted the app but has not started it yet
                        (NOT_STARTED)
      - "deploying"     replicas starting; the replica pulls the weights and
                        builds the model on the worker (DEPLOYING)
      - "running"       serving traffic (RUNNING)
      - "unhealthy"     Serve app reports UNHEALTHY
      - "failed"        Serve app DEPLOY_FAILED
      - "deleting"      Serve app being torn down (DELETING)
    """

    family: str
    suffix: str
    run_name: str
    phase: str
    message: str
    url: str | None


def model_serving_status(
    family: str, suffix: str, run_name: str
) -> ServingStatus:
    """Report the serving lifecycle phase of a model from the Ray Serve
    controller, HTTP-only.

    The serving lifecycle begins when `deploy_model` schedules the app and ends
    when `undeploy_model` tears it down; outside that window the phase is
    "not_deployed". While an app exists the phase reflects the controller's
    status ("deploying" while the replica pulls weights and builds the model on
    the worker, then "running"). See `ServingStatus` for the full vocabulary.
    The registry lifecycle is reported separately by
    `cortexgrid.model_storage.model_registry_status`.
    """
    app = get_serve_details().get("applications", {}).get(
        _app_name(family, suffix, run_name)
    )
    if app is None:
        return ServingStatus(
            family, suffix, run_name, _PHASE_NOT_DEPLOYED, "", None
        )
    raw = str(app.get("status", ""))
    return ServingStatus(
        family=family,
        suffix=suffix,
        run_name=run_name,
        phase=_serve_phase(raw),
        message=str(app.get("message", "")) or raw,
        url=f"{get_ray_serve_uri()}{_route_prefix(family, suffix, run_name)}",
    )
