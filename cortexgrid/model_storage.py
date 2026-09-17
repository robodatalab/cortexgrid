"""Model registry backed by MLflow Model Registry; weights stored directly in S3.

Mapping cortexgrid taxonomy <-> MLflow Registry:
    family + suffix     -> RegisteredModel.name = "<family>/<suffix>"
    run_name            -> ModelVersion.tags["run_name"]
    family, suffix      -> ModelVersion.tags["family"], ["suffix"]   (denormalized)
    weights blob path   -> ModelVersion.source =
                           "s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/"
    run linkage         -> ModelVersion.run_id  (built-in MLflow field; unset
                           for imported models)
    requirements        -> ModelVersion.tags["num_gpus"], ["ram_gb"], ["vram_gb"]

Two ways in: `save_model` registers a fresh copy under the calling run's
run_name every time it runs (fine-tuned output); `import_model` registers a
model produced elsewhere once, under the fixed run_name IMPORTED, and after
that only re-bundles the serve-app when its code changed. Both write the same
layout, so every
(family, suffix, run_name) consumer - load_model, deploy_model - handles both.

storage.py is pure: it takes run_id/run_name as explicit args and never reads
the active Experiment singleton. The facade that fills those in lives in
cortexgrid/__init__.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from typing import Any, Callable

from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from cortexgrid import s3_util
from cortexgrid.infra import get_mlflow_tracking_uri, get_s3_bucket
from cortexgrid.model_serving import (
    ModelRequirements,
    build_bundle,
    bundle_class,
    has_requirement_tags,
    metadata_from_tags,
    metadata_to_tags,
    requirements_from_tags,
    requirements_to_tags,
    upload_bundle,
)


# MLflow ModelVersion tag holding the registry lifecycle phase, and its
# values. This is a separate lifecycle from serving (Ray Serve); see
# cortexgrid.model_serving for that vocabulary.
_LIFECYCLE_TAG = "lifecycle"
_PHASE_UPLOADING = "uploading"
_PHASE_READY = "ready"
_PHASE_UPLOAD_FAILED = "upload_failed"
_PHASE_BROKEN = "broken"

# An upload still marked "uploading" this long after the version was created is
# treated as broken: the version is created before the weights are resolved
# (for `import_model`, before its download), so creation_timestamp is the
# start of the whole upload, and a process that dies mid-way never flips the
# tag to "ready"/"upload_failed". Expiry is
# derived lazily on read (see `_phase_for`); nothing is written back.
_UPLOAD_DEADLINE = timedelta(hours=3)

# run_name under which `import_model` registers a model: imported weights belong
# to no run, so they share one fixed key and outlive the run that imported
# them. Run names are haikunator "word-word-NN", so no run can take this name.
IMPORTED = "imported"


@dataclass
class SavedModel:
    family: str
    suffix: str
    run_name: str
    created_at: str
    data_blob_path: str
    size_bytes: int
    # Registry lifecycle phase: "uploading" while save_model streams the weights
    # and serve bundle to storage, "ready" once that finishes, "upload_failed"
    # if it errored, "broken" if an upload has stayed in progress past
    # _UPLOAD_DEADLINE (writer presumed dead). Versions written before this tag
    # existed report "ready".
    phase: str
    # Hardware one replica needs; defaults for versions stored without it.
    requirements: ModelRequirements


def _phase_for(version: Any) -> str:
    """Registry lifecycle phase of a version, expiring stale uploads to "broken".

    Reads the lifecycle tag, but an upload that has stayed "uploading" longer
    than _UPLOAD_DEADLINE (measured from creation_timestamp, i.e. the upload
    start) is reported as "broken" instead."""
    phase = version.tags.get(_LIFECYCLE_TAG, _PHASE_READY)
    if phase != _PHASE_UPLOADING:
        return phase
    started = datetime.fromtimestamp(
        version.creation_timestamp / 1000, tz=timezone.utc
    )
    if datetime.now(timezone.utc) - started > _UPLOAD_DEADLINE:
        return _PHASE_BROKEN
    return phase


def _to_saved_model(version: Any) -> SavedModel:
    return SavedModel(
        family=version.tags["family"],
        suffix=version.tags["suffix"],
        run_name=version.tags["run_name"],
        created_at=datetime.fromtimestamp(
            version.creation_timestamp / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        data_blob_path=version.source,
        size_bytes=int(version.tags.get("size_bytes", "0")),
        phase=_phase_for(version),
        requirements=requirements_from_tags(version.tags),
    )


def _dir_size_bytes(local_dir: str | Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(local_dir):
        for filename in files:
            total += os.path.getsize(os.path.join(root, filename))
    return total


def _ensure_registered_model(client: MlflowClient, name: str) -> None:
    try:
        client.get_registered_model(name)
    except MlflowException:
        client.create_registered_model(name)


def _download_s3_uri(uri: str, dest_dir: str | Path | None) -> Path:
    bucket, _, key_prefix = uri.removeprefix("s3://").partition("/")
    dest = Path(dest_dir) if dest_dir else Path(tempfile.mkdtemp())
    client = s3_util.get_s3_client()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(key_prefix) :]
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, obj["Key"], str(target))
    return dest


def save_model(
    weights_dir: str | Path,
    serve_app: type,
    suffix: str,
    family: str,
    run_id: str,
    run_name: str,
    requirements: ModelRequirements | None = None,
) -> SavedModel:
    """Upload a weights directory to S3 and register a new MLflow ModelVersion
    paired with the serve-app that fronts it.

    Meant for weights the calling run produced (e.g. a fine-tune): every run
    saves its own copy under its own run_name, so running the same code twice
    yields two models. For weights produced elsewhere, use `import_model`.

    cortexgrid stores the weights as an opaque directory: it never inspects,
    serializes, or reconstructs their contents, so the on-disk format
    (HuggingFace `save_pretrained`, `torch.save`, ONNX, anything) is entirely
    the caller's concern. That directory boundary is the open-closed extension
    point - new model kinds need no change here.

    `serve_app` is the `cortexgrid.serve.ingress` class that will front these
    weights.
    Its code is bundled and its import path, bundle URL, and pip list are
    stored as tags on the ModelVersion so `deploy_model` can bind it later
    without the caller holding the class object.

    `requirements` is the hardware one replica needs; None stores none, which
    reads as no requirement. Change it later with `set_model_requirements`."""
    return _upload_model(
        weights_dir, serve_app, suffix, family, run_id, run_name, requirements
    )


def import_model(
    source: str | Path | Callable[[], str | Path],
    serve_app: type,
    family: str,
    suffix: str,
    requirements: ModelRequirements | None = None,
) -> SavedModel:
    """Register a model produced elsewhere (e.g. a pretrained base model) under
    the fixed key (family, suffix, IMPORTED), once.

    `source` is the weights directory, or a callable returning it; the callable
    runs only when the upload actually happens, so an expensive download can be
    skipped on every run after the first. It runs after the version is
    registered as "uploading", so a concurrent import sees this one in flight
    while it downloads.

    If a version is already registered under the key:
      - "ready": returns it without calling `source`. If `serve_app`'s code no
        longer matches the stored bundle, it is re-bundled first and the
        weights are kept (see `_refresh_bundle`). `requirements` are stored
        only if the version has none yet, so values changed since with
        `set_model_requirements` are kept. To replace the weights,
        `delete_model` it first.
      - "uploading": raises RuntimeError - another process is importing it.
      - "upload_failed" / "broken": deleted and imported again.

    The version is linked to no MLflow run, so deleting a run leaves it in
    place. Deploy it like any saved model:
    `deploy_model(family, suffix, IMPORTED)`."""
    existing = model_registry_status(family, suffix, IMPORTED)
    if existing is not None:
        if existing.phase == _PHASE_READY:
            _refresh_bundle(serve_app, family, suffix)
            if requirements is not None:
                existing.requirements = _set_missing_requirements(
                    family, suffix, requirements
                )
            return existing
        if existing.phase == _PHASE_UPLOADING:
            raise RuntimeError(
                f"Model {family}/{suffix}/{IMPORTED} is being imported by "
                "another process"
            )
        delete_model(family, suffix, IMPORTED)
    return _upload_model(
        source, serve_app, suffix, family, None, IMPORTED, requirements
    )


def _refresh_bundle(serve_app: type, family: str, suffix: str) -> None:
    """Re-bundle an imported model's serve-app when its fingerprint differs
    from the bundle stored on the version, leaving the weights in place.

    The new bundle is uploaded next to the old one and the version's tags are
    pointed at it, so the next `deploy_model` runs the new code. An app that is
    already running keeps the code it started with until it is deployed again;
    the old bundle stays in storage so that app can still restart."""
    name = f"{family}__{suffix}"
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    version = client.search_model_versions(
        f"name='{name}' and tags.run_name='{IMPORTED}'"
    )[0]
    serve_bundle = build_bundle(serve_app)
    if metadata_from_tags(version.tags).fingerprint == serve_bundle.fingerprint:
        return
    meta = upload_bundle(serve_bundle, family, suffix, IMPORTED)
    for key, value in metadata_to_tags(meta).items():
        client.set_model_version_tag(name, version.version, key, value)


def _set_missing_requirements(
    family: str, suffix: str, requirements: ModelRequirements
) -> ModelRequirements:
    """Store `requirements` on an imported model whose version has none yet.
    Returns the requirements the version holds afterwards."""
    name = f"{family}__{suffix}"
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    version = client.search_model_versions(
        f"name='{name}' and tags.run_name='{IMPORTED}'"
    )[0]
    if has_requirement_tags(version.tags):
        return requirements_from_tags(version.tags)
    for key, value in requirements_to_tags(requirements).items():
        client.set_model_version_tag(name, version.version, key, value)
    return requirements


def _upload_model(
    weights: str | Path | Callable[[], str | Path],
    serve_app: type,
    suffix: str,
    family: str,
    run_id: str | None,
    run_name: str,
    requirements: ModelRequirements | None,
) -> SavedModel:
    """Register a ModelVersion in "uploading", resolve `weights` to a directory
    (calling it when it is a callable), upload the weights and the serve-app
    bundle, and flip it to "ready" (or "upload_failed")."""
    bucket = get_s3_bucket()
    prefix = f"models/{run_name}/{family}/{suffix}"
    source = f"s3://{bucket}/{prefix}/weights/"
    name = f"{family}__{suffix}"
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    _ensure_registered_model(client, name)
    # Register the version up front in the "uploading" phase, before `weights`
    # is resolved: the dashboard surfaces the model while it is still being
    # fetched and uploaded, and a concurrent `import_model` sees the import in
    # flight for the whole download instead of starting one of its own. The
    # size is stamped once the directory exists; the bundle tags and the flip
    # to "ready" happen only after the upload lands. No requirements leaves
    # their tags unset, so a later `import_model` can still store them.
    version = client.create_model_version(
        name=name,
        source=source,
        run_id=run_id,
        tags={
            "family": family,
            "suffix": suffix,
            "run_name": run_name,
            _LIFECYCLE_TAG: _PHASE_UPLOADING,
            **(requirements_to_tags(requirements) if requirements is not None else {}),
        },
    )
    try:
        weights_dir = weights() if callable(weights) else weights
        client.set_model_version_tag(
            name, version.version, "size_bytes", str(_dir_size_bytes(weights_dir))
        )
        s3_util.upload_dir(str(weights_dir), dest_path=f"{prefix}/weights")
        bundle_meta = bundle_class(serve_app, family, suffix, run_name)
        for key, value in metadata_to_tags(bundle_meta).items():
            client.set_model_version_tag(name, version.version, key, value)
        client.set_model_version_tag(
            name, version.version, _LIFECYCLE_TAG, _PHASE_READY
        )
    except Exception:
        client.set_model_version_tag(
            name, version.version, _LIFECYCLE_TAG, _PHASE_UPLOAD_FAILED
        )
        raise
    return _to_saved_model(client.get_model_version(name, version.version))


def load_model(family: str, suffix: str, run_name: str) -> Path:
    """Download a saved model's weights to a local directory and return its Path.

    cortexgrid moves an opaque directory of bytes and never interprets its
    contents; the serve-app reconstructs the model from it however it likes
    (`from_pretrained`, `torch.load`, ...). The returned directory persists
    after this call - the caller (typically a serve-app loading weights at
    startup) owns its lifetime."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    name = f"{family}__{suffix}"
    versions = client.search_model_versions(
        f"name='{name}' and tags.run_name='{run_name}'"
    )
    if not versions or not versions[0].source:
        raise ValueError(f"No model {family}/{suffix}/{run_name}")
    return _download_s3_uri(versions[0].source, None)


def list_models() -> list[SavedModel]:
    """Return SavedModel records for every ModelVersion in the registry,
    including versions still uploading or whose upload failed (see
    `SavedModel.phase`)."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    return [_to_saved_model(v) for v in client.search_model_versions("")]


def model_registry_status(
    family: str, suffix: str, run_name: str
) -> SavedModel | None:
    """Report the registry lifecycle of one model, or None if it was never
    registered (no upload ever started).

    The registry lifecycle is owned here: it begins when `save_model` creates
    the ModelVersion (`phase="uploading"`), becomes `"ready"` once the weights
    and serve bundle finish uploading, `"upload_failed"` if the upload errored,
    or `"broken"` if an upload has stayed in progress past _UPLOAD_DEADLINE
    (the writer is presumed dead). Serving is a separate lifecycle; see
    `cortexgrid.model_serving.model_serving_status`.
    """
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    versions = client.search_model_versions(
        f"name='{family}__{suffix}' and tags.run_name='{run_name}'"
    )
    return _to_saved_model(versions[0]) if versions else None


def set_model_requirements(
    family: str, suffix: str, run_name: str, requirements: ModelRequirements
) -> None:
    """Replace the hardware requirements stored on a model. Takes effect on
    its next `deploy_model`; a replica already running keeps its placement.
    Raises ValueError if the model was never registered."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    name = f"{family}__{suffix}"
    versions = client.search_model_versions(
        f"name='{name}' and tags.run_name='{run_name}'"
    )
    if not versions:
        raise ValueError(f"No model {family}/{suffix}/{run_name}")
    for key, value in requirements_to_tags(requirements).items():
        client.set_model_version_tag(name, versions[0].version, key, value)


def delete_model(family: str, suffix: str, run_name: str) -> None:
    """Delete the ModelVersion in MLflow, its weights blob, and its serve bundle."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    name = f"{family}__{suffix}"
    versions = client.search_model_versions(
        f"name='{name}' and tags.run_name='{run_name}'"
    )
    for v in versions:
        client.delete_model_version(name=v.name, version=v.version)
    s3_util.delete_prefix(f"models/{run_name}/{family}/{suffix}/")
    s3_util.delete_prefix(f"serve-bundles/{run_name}/{family}__{suffix}")


def delete_models_for_run(run_id: str) -> None:
    """Delete every ModelVersion produced by an MLflow run, plus its blobs and serve bundles."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    run = client.get_run(run_id)
    run_name = run.info.run_name or run_id
    for v in client.search_model_versions(f"run_id='{run_id}'"):
        client.delete_model_version(name=v.name, version=v.version)
    s3_util.delete_prefix(f"models/{run_name}/")
    s3_util.delete_prefix(f"serve-bundles/{run_name}/")
