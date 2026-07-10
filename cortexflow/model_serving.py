"""Cortexflow wrappers around Ray Serve.

Caller stays HTTP-only: deploy/undeploy/list talk to the Ray dashboard's
declarative `/api/serve/applications/` endpoint via [cortexflow.ray_util],
never `ray.init`. The deployment class is bundled at `save_model` time, zipped,
uploaded to MinIO under `serve-bundles/<run_name>/<family>__<suffix>.zip`, and
referenced via `runtime_env.working_dir` so Ray workers fetch it from there.
The bundle URL, class import path, and pip list are persisted as MLflow tags
on the ModelVersion so `deploy_model` can find them later without the caller
holding the class object.

Naming: the Ray Serve application is named "<family>__<suffix>__<run_name>".
This relies on family/suffix/run_name not containing the literal "__".

See [docs/cortexflow/model-serving.md](../docs/cortexflow/model-serving.md) for
the end-to-end design.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mlflow.tracking import MlflowClient
from ray.serve.schema import ApplicationStatus

from cortexflow._bundle import filter_pip_freeze, stage_bundle
from cortexflow.infra import get_mlflow_tracking_uri, get_ray_serve_uri
from cortexflow.ray_util import (
    get_serve_details,
    put_serve_applications,
)
from cortexflow.s3_util import upload
from cortexflow.secrets import get_secret


log = logging.getLogger(__name__)


# Torch wheels for the cluster GPUs. Kept in this module rather than shared
# with cortexflow.jobs: serve apps and jobs only happen to need the same wheel
# index today; the two contexts shouldn't be coupled by a borrowed constant.
_CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu128"


@dataclass
class Deployment:
    family: str
    suffix: str
    run_name: str
    url: str
    status: str


def _app_name(family: str, suffix: str, run_name: str) -> str:
    return f"{family}__{suffix}__{run_name}"


def _route_prefix(family: str, suffix: str, run_name: str) -> str:
    return f"/r/{family}/{suffix}/{run_name}"


def _pip_requirements_for_serve(external_deps: set[str]) -> list[str]:
    """Build runtime_env.pip for a Serve app: freeze + filter + drop ray +
    inject the GH token into git URLs + prepend the torch CUDA index.
    Ray will write this list to a requirements.txt on the worker."""
    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    filtered = filter_pip_freeze(freeze, external_deps)
    token = get_secret("GH_TOKEN")
    lines: list[str] = []
    for raw in filtered.splitlines():
        name = raw.lstrip().split("==", 1)[0].split(" @", 1)[0].split("[", 1)[0]
        if name.strip().lower() == "ray" or not raw.strip():
            continue
        lines.append(
            raw.replace(
                "git+https://github.com/",
                f"git+https://x-access-token:{token}@github.com/",
            )
        )
    return [f"--extra-index-url {_CUDA_INDEX_URL}", *lines]


@dataclass
class BundleMetadata:
    """What `bundle_class` produces and `deploy_model` needs to PUT the app."""

    bundle_url: str
    class_import_path: str
    pip_list: list[str]


def bundle_class(
    cls: type, family: str, suffix: str, run_name: str
) -> BundleMetadata:
    """Stage code, capture pip deps, upload bundle to MinIO.

    Returns the metadata `deploy_model` needs later; callers (typically
    `save_model`) persist it on the ModelVersion so the deploy step can run
    without holding the class object."""
    import_path = f"{cls.__module__}:{cls.__name__}"

    with stage_bundle(cls) as bundle:
        pip_list = _pip_requirements_for_serve(set(bundle.external_deps))
        log.info(
            "Serve runtime_env.pip for %s/%s/%s (%d entries):\n  %s",
            family, suffix, run_name, len(pip_list), "\n  ".join(pip_list),
        )
        with tempfile.TemporaryDirectory() as tmp:
            zip_base = Path(tmp) / f"{family}__{suffix}"
            shutil.make_archive(
                str(zip_base), "zip", root_dir=str(bundle.staging_dir)
            )
            zip_path = zip_base.with_suffix(".zip")
            bundle_url = upload(
                str(zip_path),
                dest_path=f"serve-bundles/{run_name}/{family}__{suffix}.zip",
            )

    return BundleMetadata(
        bundle_url=bundle_url,
        class_import_path=import_path,
        pip_list=pip_list,
    )


def _build_application_spec(
    family: str, suffix: str, run_name: str, meta: BundleMetadata
) -> dict[str, Any]:
    """Assemble a Ray Serve application schema from pre-bundled metadata."""
    log.info(
        "Serve runtime_env.pip for %s/%s/%s (%d entries):\n  %s",
        family, suffix, run_name, len(meta.pip_list), "\n  ".join(meta.pip_list),
    )
    return {
        "name": _app_name(family, suffix, run_name),
        "route_prefix": _route_prefix(family, suffix, run_name),
        # Ray Serve REST requires import_path to point at an Application builder
        # (callable returning a bound node) or an already-bound node. A bare
        # Deployment class is rejected, so cortexflow.deploy_model goes through
        # a generic builder that re-imports the user's class and binds it.
        "import_path": "cortexflow._serve_entry:build",
        "args": {
            "class_import_path": meta.class_import_path,
            "family": family,
            "suffix": suffix,
            "run_name": run_name,
        },
        "runtime_env": {"working_dir": meta.bundle_url, "pip": meta.pip_list},
    }


# MLflow tag keys for the bundle metadata `save_model` writes and
# `deploy_model` reads back.
_CLASS_IMPORT_PATH_TAG = "class_import_path"
_BUNDLE_URL_TAG = "serve_bundle_url"
_PIP_LIST_TAG = "serve_pip_list_json"


def metadata_to_tags(meta: BundleMetadata) -> dict[str, str]:
    """Serialise BundleMetadata to MLflow tags. The inverse of
    `_load_bundle_metadata`; lives here next to the consumer so the tag schema
    stays in one place."""
    return {
        _CLASS_IMPORT_PATH_TAG: meta.class_import_path,
        _BUNDLE_URL_TAG: meta.bundle_url,
        _PIP_LIST_TAG: json.dumps(meta.pip_list),
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
            pip_list=json.loads(tags[_PIP_LIST_TAG]),
        )
    except KeyError as exc:
        raise ValueError(
            f"Saved model {family}/{suffix}/{run_name} is missing the deployment "
            f"bundle tag {exc.args[0]!r}; re-save with `cortexflow.save_model`."
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
    request schemas - against that URL; cortexflow imposes no traffic contract.

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
        status=str(app.get("status", "unknown")),
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
                status=str(app.get("status", "unknown")),
            )
        )
    return result


@dataclass
class ServingStatus:
    """Serving lifecycle of one model, owned by the Ray Serve controller.

    This is the serving half of a model's life. The registry half (uploading /
    ready in MLflow) is a separate lifecycle reported by
    `cortexflow.model_storage.model_registry_status`.

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


_PHASE_NOT_DEPLOYED = "not_deployed"

_PHASE_BY_SERVE_STATUS = {
    ApplicationStatus.RUNNING.value: "running",
    ApplicationStatus.DEPLOYING.value: "deploying",
    ApplicationStatus.NOT_STARTED.value: "not_started",
    ApplicationStatus.UNHEALTHY.value: "unhealthy",
    ApplicationStatus.DEPLOY_FAILED.value: "failed",
    ApplicationStatus.DELETING.value: "deleting",
}


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
    `cortexflow.model_storage.model_registry_status`.
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
        phase=_PHASE_BY_SERVE_STATUS.get(raw, "deploying"),
        message=str(app.get("message", "")) or raw,
        url=f"{get_ray_serve_uri()}{_route_prefix(family, suffix, run_name)}",
    )
