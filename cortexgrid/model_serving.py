"""Cortexgrid wrappers around Ray Serve.

Caller stays HTTP-only: deploy/undeploy/list talk to the Ray dashboard's
declarative `/api/serve/applications/` endpoint via [cortexgrid.ray_util],
never `ray.init`. The deployment class is bundled at `save_model` time, zipped,
uploaded to MinIO under
`serve-bundles/<run_name>/<family>__<suffix>/<fingerprint>.zip`, and
referenced via `runtime_env.working_dir` so Ray workers fetch it from there.
The bundle URL, class import path, pip list, and fingerprint are persisted as
tags on the model's registry entry so `deploy_model` can find them later
without the caller holding the class object. So are the model's
`ModelRequirements`, which `deploy_model` turns into the replica's Ray resource
requests.

Every model `deploy_model` puts on Ray Serve gets a deployment record with the
jobs control plane: the spec it PUT, plus the phase, message and replica
placements the control plane last observed (`observe_deployments`, run on
every poll cycle). Listings and status reads come from those records; waits
ask the Serve controller directly.

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
from dataclasses import asdict, dataclass, field
import hashlib
from pathlib import Path
from typing import Any

from ray.serve.schema import ApplicationStatus, ReplicaState

from cortexgrid import state
from cortexgrid._bundle import bundle, digest, stage, worker_provides
from cortexgrid.infra import get_ray_serve_uri
from cortexgrid.ray_util import (
    get_ray_nodes,
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


def app_name(family: str, suffix: str, run_name: str) -> str:
    return f"{family}__{suffix}__{run_name}"


def _route_prefix(family: str, suffix: str, run_name: str) -> str:
    return f"/r/{family}/{suffix}/{run_name}"


_PHASE_NOT_DEPLOYED = "not_deployed"
_PHASE_PAUSED = "paused"

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
    `save_model`) persist it on the registry entry so the deploy step can run
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


# Custom Ray resource each GPU worker advertises: the MiB of memory its GPUs
# have (see the ray-worker DaemonSet). A replica requests its vram_gb of it in
# MiB, so Ray places it only on a node with that much VRAM left. MiB because
# nvidia-smi reports MiB and a GPU's memory is not a whole number of GiB.
_VRAM_RESOURCE = "vram_mib"

_MIB_PER_GIB = 1024

_GIB = 1024**3


# Node label each GPU worker sets to the same MiB it advertises as the
# `_VRAM_RESOURCE` (see the ray-worker DaemonSet). The resource reserves VRAM;
# the label names the size class of the node's GPUs, which is what lets a
# replica ask for the smallest card that fits. Nodes with no GPU carry neither.
_VRAM_LABEL = "vram_mib"


def vram_tiers() -> list[int]:
    """The distinct GPU sizes in the cluster, in MiB, smallest first.

    One entry per size class, not per node: two 12 GiB workers are one tier.
    Nodes that are not ALIVE, and nodes with no `vram_mib` label (CPU workers,
    the head), contribute none - so a cluster with no GPUs reports no tiers.
    """
    tiers = set()
    for node in get_ray_nodes():
        if node.get("state") != "ALIVE":
            continue
        label = (node.get("labels") or {}).get(_VRAM_LABEL)
        # A worker that could not size its GPUs never starts, so a malformed
        # label means someone set it by hand; skip it rather than fail every
        # deploy in the cluster.
        if label is not None and label.isdigit():
            tiers.add(int(label))
    return sorted(tiers)


def _placement_preferences(needed_mib: int, tiers: list[int]) -> list[dict[str, Any]]:
    """The size classes a replica needing `needed_mib` should be offered, best
    first: every tier large enough, smallest first, then a catch-all for any
    tier that is not too small.

    The catch-all is what keeps this from going stale. It excludes the sizes
    known to be too small rather than naming the ones that fit, so a larger
    GPU joining the cluster after this deploy is still placeable without a
    redeploy. It is dropped when no known tier is too small, since there is
    then nothing left for it to say.

    Excluding rather than naming also matches a node carrying no `vram_mib`
    label at all. That is harmless: a model reaching here has VRAM to reserve,
    so it also requests `num_gpus` and `_VRAM_RESOURCE`, neither of which a
    CPU-only node has. The resource request, not the selector, is what keeps
    a GPU model off a CPU node.
    """
    # Sorted here rather than trusted from the caller: the whole contract is
    # "smallest first", and it must not rest on how the tiers arrived.
    ordered = sorted(tiers)
    fits = [tier for tier in ordered if tier >= needed_mib]
    too_small = [str(tier) for tier in ordered if tier < needed_mib]
    preferences = [{_VRAM_LABEL: str(tier)} for tier in fits]
    if too_small:
        preferences.append({_VRAM_LABEL: f"!in({', '.join(too_small)})"})
    return preferences


def _placement_options(
    requirements: ModelRequirements, tiers: list[int]
) -> dict[str, Any]:
    """Ask Ray for the smallest GPU that fits, falling back to larger ones.

    `label_selector` names the smallest size class the model fits on, and
    `fallback_strategy` the larger ones in order, so Ray reaches for a bigger
    card only once every smaller one is out of VRAM. That a fallback fires on
    exhaustion, and not merely on a size class being absent, is what makes
    this "smallest that is free" rather than "smallest that exists"; checked
    against Ray 2.58, which is the floor this package pins for it.

    These only order the candidates. The reservation is still the `vram_mib`
    resource, so two replicas can no more share a card's memory than before,
    and a selector matching nothing leaves the replica pending exactly as an
    unsatisfiable resource request does.

    A model with no VRAM requirement gets no selector at all, so it stays
    placeable on a CPU-only node - and so does every model when the cluster
    reports no GPU sizes, which is the pre-label behaviour.
    """
    if requirements.vram_gb <= 0 or not tiers:
        return {}
    preferences = _placement_preferences(
        round(requirements.vram_gb * _MIB_PER_GIB), tiers
    )
    first, *rest = preferences
    options: dict[str, Any] = {"label_selector": first}
    if rest:
        options["fallback_strategy"] = [{"label_selector": r} for r in rest]
    return options


def _ray_actor_options(
    requirements: ModelRequirements, tiers: list[int]
) -> dict[str, Any]:
    """Translate ModelRequirements into a replica's Ray actor resource requests.
    Ray places the replica only on a node with that much free and reserves it
    there; a zero requirement requests nothing. `tiers` are the cluster's GPU
    size classes, which decide which node Ray prefers among those that fit."""
    options: dict[str, Any] = {"num_gpus": requirements.num_gpus}
    if requirements.ram_gb > 0:
        options["memory"] = int(requirements.ram_gb * _GIB)
    if requirements.vram_gb > 0:
        options["resources"] = {
            _VRAM_RESOURCE: round(requirements.vram_gb * _MIB_PER_GIB)
        }
    options.update(_placement_options(requirements, tiers))
    return options


def _build_application_spec(
    family: str,
    suffix: str,
    run_name: str,
    meta: BundleMetadata,
    requirements: ModelRequirements,
    num_replicas: int,
    tiers: list[int],
) -> dict[str, Any]:
    """Assemble a Ray Serve application schema from pre-bundled metadata, the
    model's requirements, and the cluster's GPU size classes."""
    # working_dir carries the serve-app's own source; Ray pip-installs the
    # third-party distributions the image lacks into a per-node cached
    # virtualenv layered on the image. No pip key when there are none, so Ray
    # builds no virtualenv.
    runtime_env: dict[str, Any] = {"working_dir": meta.bundle_url}
    if meta.pip_requirements:
        runtime_env["pip"] = meta.pip_requirements
    return {
        "name": app_name(family, suffix, run_name),
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
            "num_replicas": num_replicas,
            "ray_actor_options": _ray_actor_options(requirements, tiers),
        },
        "runtime_env": runtime_env,
    }


# Registry tag keys for the bundle metadata `save_model` writes and
# `deploy_model` reads back.
_CLASS_IMPORT_PATH_TAG = "class_import_path"
_BUNDLE_URL_TAG = "serve_bundle_url"
_PIP_REQUIREMENTS_TAG = "serve_pip_requirements"
_BUNDLE_FINGERPRINT_TAG = "serve_bundle_fingerprint"


def metadata_to_tags(meta: BundleMetadata) -> dict[str, str]:
    """Serialise BundleMetadata to registry tags. The inverse of
    `metadata_from_tags`; lives here next to the consumer so the tag schema
    stays in one place."""
    return {
        _CLASS_IMPORT_PATH_TAG: meta.class_import_path,
        _BUNDLE_URL_TAG: meta.bundle_url,
        _PIP_REQUIREMENTS_TAG: json.dumps(meta.pip_requirements),
        _BUNDLE_FINGERPRINT_TAG: meta.fingerprint,
    }


def metadata_from_tags(tags: dict[str, str]) -> BundleMetadata:
    """Deserialise BundleMetadata from a registry entry's tags. Raises
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
    """Hardware one replica of a model needs to be served, in GiB. Persisted as
    tags on the registry entry next to the bundle metadata, so it is read without
    touching the weights or importing the serve-app class.

    Zero means no requirement: a model with no requirements is served on any
    node, CPU-only included. Models saved before requirements existed carry
    no tags and read as the defaults."""

    # Fractional, so several models can share one card: 0.25 puts four
    # replicas on a GPU. Ray does not isolate them - they all see the same
    # device - so what keeps them from overcommitting its memory is `vram_gb`,
    # which is reserved from the card's `vram_mib` and is the real limit.
    num_gpus: float = 0.0
    ram_gb: float = 0.0
    # GPU memory across the replica's num_gpus GPUs, so it needs a GPU share.
    vram_gb: float = 0.0

    def __post_init__(self) -> None:
        if self.num_gpus < 0 or self.ram_gb < 0 or self.vram_gb < 0:
            raise ValueError(f"Model requirements cannot be negative: {self}")
        if self.vram_gb > 0 and self.num_gpus == 0:
            raise ValueError(
                f"vram_gb={self.vram_gb} needs a GPU; set num_gpus > 0"
            )


# Registry tag keys for the ModelRequirements.
_NUM_GPUS_TAG = "num_gpus"
_RAM_GB_TAG = "ram_gb"
_VRAM_GB_TAG = "vram_gb"


def has_requirement_tags(tags: dict[str, str]) -> bool:
    """Whether requirements were ever stored on the registry entry."""
    return any(key in tags for key in (_NUM_GPUS_TAG, _RAM_GB_TAG, _VRAM_GB_TAG))


def requirements_to_tags(requirements: ModelRequirements) -> dict[str, str]:
    """Serialise ModelRequirements to registry tags. The inverse of
    `requirements_from_tags`."""
    return {
        _NUM_GPUS_TAG: str(requirements.num_gpus),
        _RAM_GB_TAG: str(requirements.ram_gb),
        _VRAM_GB_TAG: str(requirements.vram_gb),
    }


def requirements_from_tags(tags: dict[str, str]) -> ModelRequirements:
    """Deserialise ModelRequirements from a registry entry's tags; a
    missing tag reads as no requirement."""
    return ModelRequirements(
        # float, not int: models saved before GPUs could be shared stored a
        # whole number, which parses as one.
        num_gpus=float(tags.get(_NUM_GPUS_TAG, "0")),
        ram_gb=float(tags.get(_RAM_GB_TAG, "0")),
        vram_gb=float(tags.get(_VRAM_GB_TAG, "0")),
    )


def _load_deploy_metadata(
    family: str, suffix: str, run_name: str
) -> tuple[BundleMetadata, ModelRequirements]:
    """Read the bundle metadata and requirements `save_model` persisted on the
    registry entry."""
    record = state.get("models", family, suffix, run_name)
    if record is None:
        raise ValueError(
            f"No saved model for {family}/{suffix}/{run_name}; cannot deploy."
        )
    tags = record["tags"]
    try:
        return metadata_from_tags(tags), requirements_from_tags(tags)
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
        app_name(family, suffix, run_name), timeout, _deadline(timeout)
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
    name = app_name(family, suffix, run_name)
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
_LIVE_PHASES = ("running", "deploying", "not_started", "unhealthy", _PHASE_PAUSED)


def _record_is_current(
    record: dict[str, Any] | None,
    family: str,
    suffix: str,
    run_name: str,
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
    if record is None or record["phase"] not in _LIVE_PHASES:
        return False
    spec = _build_application_spec(
        family, suffix, run_name, meta, requirements, num_replicas, record["tiers"]
    )
    return spec == record["spec"]


def deploy_model(
    family: str,
    suffix: str,
    run_name: str,
    num_replicas: int = 1,
    wait: bool = False,
    timeout: float | None = 300.0,
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

    Records the deployment - the spec it PUT, where the app is served, and its
    phase - with the jobs control plane.

    With `wait=True`, blocks as `wait_for_model_serving` does until the Serve
    controller reports the app RUNNING. `timeout` (default 300) caps the whole
    call, clearing a failed app included; exceeding it raises TimeoutError, and
    DEPLOY_FAILED raises ModelDeployFailed. With `timeout=None` there is no cap.
    Tradeoff: an app that never reaches a terminal state (e.g. GPU-starved,
    stuck in DEPLOYING) will hang forever.
    """
    deadline = _deadline(timeout)
    meta, requirements = _load_deploy_metadata(family, suffix, run_name)
    record = state.get("deployments", family, suffix, run_name)
    if _record_is_current(
        record, family, suffix, run_name, meta, requirements, num_replicas
    ):
        phase = record["phase"]
        if wait:
            _wait_for_application_running(record["spec"]["name"], timeout, deadline)
            phase = _observed(
                get_serve_details().get("applications", {}).get(record["spec"]["name"])
            )["phase"]
        return Deployment(
            family=family,
            suffix=suffix,
            run_name=run_name,
            url=record["url"],
            phase=phase,
        )
    # Read afresh on every deploy that reaches Ray: the tiers are what the
    # model is placed against, so a GPU joining or leaving the cluster has to
    # change the spec (and therefore re-PUT it).
    tiers = vram_tiers()
    spec = _build_application_spec(
        family, suffix, run_name, meta, requirements, num_replicas, tiers
    )
    _clear_failed_application(family, suffix, run_name, timeout, deadline)
    if not _spec_already_deployed(spec):
        existing = [
            a for a in _current_application_specs() if a["name"] != spec["name"]
        ]
        # The controller registers the app, sets it DEPLOYING and stamps
        # last_deployed_time_s before the PUT returns, so the wait below neither
        # misses the app nor reads a status left by an earlier deploy.
        put_serve_applications([*existing, spec])
    if wait:
        _wait_for_application_running(spec["name"], timeout, deadline)
    app = get_serve_details().get("applications", {}).get(spec["name"], {})
    url = f"{get_ray_serve_uri()}{_route_prefix(family, suffix, run_name)}"
    observed = _observed(app)
    state.put(
        "deployments",
        family,
        suffix,
        run_name,
        body={"spec": spec, "tiers": tiers, "url": url, **observed},
    )
    return Deployment(
        family=family,
        suffix=suffix,
        run_name=run_name,
        url=url,
        phase=observed["phase"],
    )


def undeploy_model(family: str, suffix: str, run_name: str) -> None:
    """Tear down the Ray Serve app for this model and drop its deployment record."""
    name = app_name(family, suffix, run_name)
    remaining = [a for a in _current_application_specs() if a["name"] != name]
    put_serve_applications(remaining)
    state.delete("deployments", family, suffix, run_name)


def list_deployed_models() -> list[Deployment]:
    """Return a Deployment for every model `deploy_model` put on Ray Serve
    whose app the control plane last saw existing."""
    return [
        Deployment(
            family=record["family"],
            suffix=record["suffix"],
            run_name=record["run_name"],
            url=record["url"],
            phase=record["phase"],
        )
        for record in state.get("deployments")
        if record["phase"] != _PHASE_NOT_DEPLOYED
    ]


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

    family: str
    suffix: str
    run_name: str
    phase: str
    message: str
    url: str | None


def model_serving_status(
    family: str, suffix: str, run_name: str
) -> ServingStatus:
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
    record = state.get("deployments", family, suffix, run_name)
    if record is None or record["phase"] == _PHASE_NOT_DEPLOYED:
        return ServingStatus(
            family, suffix, run_name, _PHASE_NOT_DEPLOYED, "", None
        )
    return ServingStatus(
        family=family,
        suffix=suffix,
        run_name=run_name,
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


def model_replica_placements(
    family: str, suffix: str, run_name: str
) -> list[ReplicaPlacement]:
    """Report which worker each of a model's replicas is running on.

    This is what the requirements and the size-class preferences actually
    resolved to: `deploy_model` asks for the smallest GPU that fits, and this
    is the card it got. Read from the deployment record, as the control plane
    last observed it. Empty when no app exists, and while an app is
    `deploying` it fills in as replicas are placed.
    """
    record = state.get("deployments", family, suffix, run_name)
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


def _observed(app: dict[str, Any] | None) -> dict[str, Any]:
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
        return _PHASE_PAUSED
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
    for record in state.get("deployments"):
        family, suffix, run_name = record["family"], record["suffix"], record["run_name"]
        observed = _observed(applications.get(app_name(family, suffix, run_name)))
        if any(record[key] != value for key, value in observed.items()):
            state.patch("deployments", family, suffix, run_name, body=observed)


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
        app_name(family, suffix, run_name)
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
