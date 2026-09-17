"""Cortexgrid wrappers around Ray Serve.

Caller stays HTTP-only: deploy/undeploy/list talk to the Ray dashboard's
declarative `/api/serve/applications/` endpoint via [cortexgrid.ray_util],
never `ray.init`. The deployment class is bundled at `save_model` time, zipped,
uploaded to MinIO under
`serve-bundles/<run_name>/<family>__<suffix>/<fingerprint>.zip`, and
referenced via `runtime_env.working_dir` so Ray workers fetch it from there.
The bundle URL, class import path, pip list, and fingerprint are persisted as
MLflow tags on the ModelVersion so `deploy_model` can find them later without
the caller holding the class object.

Naming: the Ray Serve application is named "<family>__<suffix>__<run_name>".
This relies on family/suffix/run_name not containing the literal "__".

See [docs/cortexgrid/model-serving.md](../docs/cortexgrid/model-serving.md) for
the end-to-end design.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any

from mlflow.tracking import MlflowClient
from ray.serve.schema import ApplicationStatus

from cortexgrid._bundle import bundle, digest, stage, worker_provides
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
    # ServeBundle.fingerprint of the uploaded bundle; empty for models saved
    # before bundles were fingerprinted
    fingerprint: str = ""


@dataclass
class ServeBundle:
    """A serve-app's bundle, resolved locally but not uploaded yet: what
    `build_bundle` finds and `upload_bundle` ships."""

    files: set[Path]
    class_import_path: str
    pip_requirements: list[str]
    # Hash of everything the replica runs: the staged files, the class it
    # imports, and the requirements it installs. Equal fingerprints mean the
    # same code, so an uploaded bundle can be reused.
    fingerprint: str


def build_bundle(cls: type) -> ServeBundle:
    """Resolve the serve-app class's code (and the serve entrypoint) into a
    bundle, without uploading it.

    Raises ValueError for a class wrapped by `ray.serve.ingress`: that wrapper
    is a subclass Ray defines in its own module, and on older Ray (e.g. 2.9) it
    reports that module as its own, so the class's source and import path would
    resolve to Ray instead of the serve-app. `cortexgrid.serve.ingress` leaves
    the class unwrapped."""
    if any(klass.__module__.startswith("ray.serve") for klass in cls.__mro__):
        raise ValueError(
            f"{cls.__name__} is wrapped by ray.serve.ingress; decorate it with "
            "cortexgrid.serve.ingress instead (from cortexgrid import serve)"
        )
    entry_file = Path(inspect.getfile(cls)).resolve()
    serve_entry = Path(__file__).with_name("_serve_entry.py")
    desc = bundle(entry_file).merge(bundle(serve_entry))
    class_import_path = f"{cls.__module__}:{cls.__name__}"
    pip_requirements = desc.pip_requirements(worker_provides())
    fingerprint = hashlib.sha256(
        json.dumps(
            [digest(desc.local_files), class_import_path, pip_requirements]
        ).encode()
    ).hexdigest()
    return ServeBundle(
        files=desc.local_files,
        class_import_path=class_import_path,
        pip_requirements=pip_requirements,
        fingerprint=fingerprint,
    )


def bundle_class(
    cls: type, family: str, suffix: str, run_name: str
) -> BundleMetadata:
    """Bundle the serve-app class's code (and the serve entrypoint), zip it, and
    upload to MinIO: `build_bundle` followed by `upload_bundle`.

    Returns the metadata `deploy_model` needs later; callers (typically
    `save_model`) persist it on the ModelVersion so the deploy step can run
    without holding the class object."""
    return upload_bundle(build_bundle(cls), family, suffix, run_name)


def upload_bundle(
    serve_bundle: ServeBundle, family: str, suffix: str, run_name: str
) -> BundleMetadata:
    """Zip a built bundle and upload it under its fingerprint.

    The fingerprint is part of the URL because Ray keeps a remote working_dir
    it has downloaded and reuses it for the same URL: new code at an old URL
    would never reach a replica."""
    with tempfile.TemporaryDirectory() as tmp:
        code_root = Path(tmp) / "code"
        stage(serve_bundle.files, code_root)
        log.info(
            "Serve bundle for %s/%s/%s: %d files, pip: %s",
            family,
            suffix,
            run_name,
            len(serve_bundle.files),
            serve_bundle.pip_requirements,
        )
        # Ray unpacks a remote (s3://) working_dir zip by stripping its
        # top-level directory when there is exactly one, so a bundle of a single
        # package (model_gateway/) would lose that directory and its import
        # path. Zipping under code/ gives Ray that one directory to strip.
        # make_archive returns the path it wrote. Rebuilding it with
        # Path.with_suffix would cut dotted names ("Qwen2.5-0.5B" -> "Qwen2.zip").
        archive = shutil.make_archive(
            str(Path(tmp) / f"{family}__{suffix}"),
            "zip",
            root_dir=tmp,
            base_dir=code_root.name,
        )
        bundle_url = upload(
            archive,
            dest_path=(
                f"serve-bundles/{run_name}/{family}__{suffix}/"
                f"{serve_bundle.fingerprint}.zip"
            ),
        )
    return BundleMetadata(
        bundle_url=bundle_url,
        class_import_path=serve_bundle.class_import_path,
        pip_requirements=serve_bundle.pip_requirements,
        fingerprint=serve_bundle.fingerprint,
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
_BUNDLE_FINGERPRINT_TAG = "serve_bundle_fingerprint"


def metadata_to_tags(meta: BundleMetadata) -> dict[str, str]:
    """Serialise BundleMetadata to MLflow tags. The inverse of
    `metadata_from_tags`; lives here next to the consumer so the tag schema
    stays in one place."""
    return {
        _CLASS_IMPORT_PATH_TAG: meta.class_import_path,
        _BUNDLE_URL_TAG: meta.bundle_url,
        _PIP_REQUIREMENTS_TAG: json.dumps(meta.pip_requirements),
        _BUNDLE_FINGERPRINT_TAG: meta.fingerprint,
    }


def metadata_from_tags(tags: dict[str, str]) -> BundleMetadata:
    """Deserialise BundleMetadata from a ModelVersion's MLflow tags. Raises
    KeyError for a missing bundle URL or class import path."""
    return BundleMetadata(
        bundle_url=tags[_BUNDLE_URL_TAG],
        class_import_path=tags[_CLASS_IMPORT_PATH_TAG],
        # Absent on models saved before dependencies were pip-installed.
        pip_requirements=json.loads(tags.get(_PIP_REQUIREMENTS_TAG, "[]")),
        # Absent on models saved before bundles were fingerprinted.
        fingerprint=tags.get(_BUNDLE_FINGERPRINT_TAG, ""),
    )


@dataclass
class ModelRequirements:
    """Hardware one replica of a model needs to be served. Persisted as tags on
    the ModelVersion next to the bundle metadata, so it is read without
    touching the weights or importing the serve-app class.

    Zero means no requirement: a model with no requirements is served on any
    node, CPU-only included. Models saved before requirements existed carry
    no tags and read as the defaults."""

    num_gpus: int = 0
    ram_gb: float = 0.0
    # Memory of the GPU the replica runs on, so it needs num_gpus >= 1.
    vram_gb: float = 0.0

    def __post_init__(self) -> None:
        if self.num_gpus < 0 or self.ram_gb < 0 or self.vram_gb < 0:
            raise ValueError(f"Model requirements cannot be negative: {self}")
        if self.vram_gb > 0 and self.num_gpus == 0:
            raise ValueError(
                f"vram_gb={self.vram_gb} needs a GPU; set num_gpus >= 1"
            )


# MLflow tag keys for the ModelRequirements.
_NUM_GPUS_TAG = "num_gpus"
_RAM_GB_TAG = "ram_gb"
_VRAM_GB_TAG = "vram_gb"


def has_requirement_tags(tags: dict[str, str]) -> bool:
    """Whether requirements were ever stored on the ModelVersion."""
    return any(key in tags for key in (_NUM_GPUS_TAG, _RAM_GB_TAG, _VRAM_GB_TAG))


def requirements_to_tags(requirements: ModelRequirements) -> dict[str, str]:
    """Serialise ModelRequirements to MLflow tags. The inverse of
    `requirements_from_tags`."""
    return {
        _NUM_GPUS_TAG: str(requirements.num_gpus),
        _RAM_GB_TAG: str(requirements.ram_gb),
        _VRAM_GB_TAG: str(requirements.vram_gb),
    }


def requirements_from_tags(tags: dict[str, str]) -> ModelRequirements:
    """Deserialise ModelRequirements from a ModelVersion's MLflow tags; a
    missing tag reads as no requirement."""
    return ModelRequirements(
        num_gpus=int(tags.get(_NUM_GPUS_TAG, "0")),
        ram_gb=float(tags.get(_RAM_GB_TAG, "0")),
        vram_gb=float(tags.get(_VRAM_GB_TAG, "0")),
    )


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
    try:
        return metadata_from_tags(versions[0].tags or {})
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


class ModelDeployFailed(RuntimeError):
    """A model's Serve app cannot reach RUNNING: the controller reported
    DEPLOY_FAILED, or no app exists for the model. Subclasses RuntimeError, which
    `deploy_model(wait=True)` raised before this type existed."""


_SERVING_POLL_INTERVAL_S = 2.0


def _deadline(timeout: float | None) -> float | None:
    return None if timeout is None else time.monotonic() + timeout


def _past(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def wait_for_model_serving(
    family: str, suffix: str, run_name: str, timeout: float | None = None
) -> None:
    """Block until the model's Serve app is RUNNING.

    Raises ModelDeployFailed on DEPLOY_FAILED, carrying the controller's message,
    and as soon as no app exists for the model: never deployed, undeployed, or
    dropped by a concurrent `deploy_model` (each one GETs the applications list,
    splices its own app in and PUTs the whole list back, so a later PUT can drop
    an app an earlier one added). NOT_STARTED, DEPLOYING, UNHEALTHY and DELETING
    are transient; a DELETING app ends up missing. Exceeding a finite `timeout`
    raises TimeoutError; with `timeout=None` there is no deadline.
    """
    _wait_for_application_running(
        _app_name(family, suffix, run_name), timeout, _deadline(timeout)
    )


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
    family: str, suffix: str, run_name: str, timeout: float | None, deadline: float | None
) -> None:
    """Remove the model's DEPLOY_FAILED Serve app, and wait until it, or an app
    already DELETING, is gone.

    Ray resets a failed deployment only when a deploy arrives after the
    deployment is marked for deletion or its version changes. Re-PUTting an
    identical spec over a failed app, or PUTting it back before the controller's
    next tick has processed an undeploy, leaves the failed deployment in place,
    and the app reports DEPLOY_FAILED again without retrying."""
    name = _app_name(family, suffix, run_name)
    app = get_serve_details().get("applications", {}).get(name)
    if app is None:
        return
    status = app.get("status")
    if status == ApplicationStatus.DEPLOY_FAILED.value:
        undeploy_model(family, suffix, run_name)
    elif status != ApplicationStatus.DELETING.value:
        return
    while name in get_serve_details().get("applications", {}):
        if _past(deadline):
            raise TimeoutError(
                f"Serve app {name!r} was not removed within {timeout}s"
            )
        time.sleep(_SERVING_POLL_INTERVAL_S)


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

    A DEPLOY_FAILED app left by an earlier attempt is undeployed first, and it,
    or an app still DELETING, is waited out before the new spec is PUT, so the
    deploy starts afresh instead of Ray reusing the failed deployment.

    With `wait=True`, blocks as `wait_for_model_serving` does until the Serve
    controller reports the app RUNNING. `timeout` (default 300) caps the whole
    call, clearing a failed app included; exceeding it raises TimeoutError, and
    DEPLOY_FAILED raises ModelDeployFailed. With `timeout=None` there is no cap.
    Tradeoff: an app that never reaches a terminal state (e.g. GPU-starved,
    stuck in DEPLOYING) will hang forever.
    """
    deadline = _deadline(timeout)
    meta = _load_bundle_metadata(family, suffix, run_name)
    spec = _build_application_spec(family, suffix, run_name, meta)
    _clear_failed_application(family, suffix, run_name, timeout, deadline)
    existing = [a for a in _current_application_specs() if a["name"] != spec["name"]]
    # The controller registers the app, sets it DEPLOYING and stamps
    # last_deployed_time_s before the PUT returns, so the wait below neither
    # misses the app nor reads a status left by an earlier deploy.
    put_serve_applications([*existing, spec])
    if wait:
        _wait_for_application_running(spec["name"], timeout, deadline)
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


def model_serving_messages(
    family: str, suffix: str, run_name: str
) -> list[ServingMessage]:
    """Return the non-empty controller messages for a model's Serve app: the
    app-level message first, then each deployment's. Empty when no app exists.
    This is where Ray explains a DEPLOY_FAILED or UNHEALTHY app."""
    app = get_serve_details().get("applications", {}).get(
        _app_name(family, suffix, run_name)
    )
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
