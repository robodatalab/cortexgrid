"""Model registry kept by the jobs control plane; weights stored directly in S3.

One registry entry (a `models` row) per (family, suffix, run_name):
    family, suffix,
    run_name            -> the entry's key, and tags["family"], ["suffix"],
                           ["run_name"]   (denormalized)
    weights blob path   -> source =
                           "s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/"
                           (NO_WEIGHTS for a model registered without any)
    run linkage         -> run_id  (unset for imported models)
    requirements        -> tags["num_gpus"], ["ram_gb"], ["vram_gb"]
    config              -> tags["config"]  (JSON object)

Three ways in: `save_model` registers a fresh copy under the calling run's
run_name every time it runs (fine-tuned output); `import_model` registers a
model produced elsewhere once, under the fixed run_name IMPORTED, and after
that only re-bundles the serve-app when its code changed; `register_model`
registers a model that stages no weights at all - a serve-app that forwards to
a hosted API holds none - under that same fixed key. All three write the same
layout, so every (family, suffix, run_name) consumer - deploy_model, the
dashboard - handles them alike; only `load_model` parts them, having nothing to
hand back for a model registered without weights.

storage.py is pure: it takes run_id/run_name as explicit args and never reads
the active Experiment singleton. The facade that fills those in lives in
cortexgrid/__init__.py.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from typing import Any, Callable

from cortexgrid import s3_util, state
from cortexgrid.infra import get_s3_bucket
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


# Registry tag holding the registry lifecycle phase, and its values. This is a separate lifecycle from serving (Ray Serve); see
# cortexgrid.model_serving for that vocabulary.
_LIFECYCLE_TAG = "lifecycle"
_PHASE_UPLOADING = "uploading"
_PHASE_READY = "ready"
_PHASE_UPLOAD_FAILED = "upload_failed"
_PHASE_BROKEN = "broken"

# Registry tag holding the model's config: whatever settings its
# serve-app needs that are not the weights (a provider's model id, an endpoint,
# the name of a secret to read). cortexgrid never interprets it - it is the
# serve-app's own vocabulary, stored next to the model so the app reads it at
# construction instead of being redeployed to change a setting.
#
# One JSON object in one tag, not a tag per key: the keys are the serve-app's
# to choose, and the whole mapping is replaced in a single write, so removing
# a key needs no tag deletion.
_CONFIG_TAG = "config"


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

# Registry source of a model registered with no weights of its own: nothing
# was staged, so there is no blob to point at. Spelled as a URI rather than left
# blank so every reader - `load_model`, the dashboard's storage field - sees
# why there is no path instead of an empty one.
NO_WEIGHTS = "cortexgrid://no-weights"


@dataclass
class SavedModel:
    family: str
    suffix: str
    run_name: str
    created_at: str
    # Where the weights live, or NO_WEIGHTS for a model registered without any.
    data_blob_path: str
    # Size of the weights; 0 for a model registered without any.
    size_bytes: int
    # Registry lifecycle phase: "uploading" while save_model streams the weights
    # and serve bundle to storage, "ready" once that finishes, "upload_failed"
    # if it errored, "broken" if an upload has stayed in progress past
    # _UPLOAD_DEADLINE (writer presumed dead). Versions written before this tag
    # existed report "ready".
    phase: str
    # Hardware one replica needs; defaults for versions stored without it.
    requirements: ModelRequirements
    # Free-form settings the serve-app reads at construction; empty for
    # versions stored without any.
    config: dict[str, str] = field(default_factory=dict)

    @property
    def has_weights(self) -> bool:
        """Whether this model staged weights of its own. False for one
        registered with `register_model`, whose serve-app holds no bytes to
        store; `load_model` on it raises."""
        return self.data_blob_path != NO_WEIGHTS


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


def _config_to_tag(config: dict[str, str]) -> str:
    """Serialise a config mapping to its registry tag value.

    Keys and values are strings: they round-trip through a tag, and the model
    card edits them as text. A caller with a number or a flag spells it as a
    string and the serve-app parses it back."""
    for key, value in config.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(
                f"Model config must map strings to strings: {key!r}: {value!r}"
            )
        if not key.strip():
            raise ValueError("Model config keys cannot be blank")
    return json.dumps(config)


def _config_from_tags(tags: dict[str, str]) -> dict[str, str]:
    """Deserialise a config mapping from a registry entry's tags; a
    missing tag reads as no config."""
    return json.loads(tags.get(_CONFIG_TAG, "{}"))


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
        config=_config_from_tags(version.tags),
    )


def _dir_size_bytes(local_dir: str | Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(local_dir):
        for filename in files:
            total += os.path.getsize(os.path.join(root, filename))
    return total


@dataclass
class _ModelVersion:
    """A registry entry as the control plane returns it."""

    tags: dict[str, str]
    source: str
    run_id: str | None
    creation_timestamp: int  # ms since the epoch


def _get_version(family: str, suffix: str, run_name: str) -> _ModelVersion | None:
    record = state.get("models", family, suffix, run_name)
    return None if record is None else _ModelVersion(**record)


def _set_tags(family: str, suffix: str, run_name: str, tags: dict[str, str]) -> bool:
    """Merge `tags` into the entry's. False if there is no such entry."""
    return state.patch("models", family, suffix, run_name, "tags", body=tags)


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
    config: dict[str, str] | None = None,
) -> SavedModel:
    """Upload a weights directory to S3 and register it paired with the
    serve-app that fronts it.

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
    stored as tags on the registry entry so `deploy_model` can bind it later
    without the caller holding the class object.

    `requirements` is the hardware one replica needs; None stores none, which
    reads as no requirement. Change it later with `set_model_requirements`.

    `config` is whatever else the serve-app needs to know about this model,
    as a string mapping it reads with `model_config` at construction; None
    stores none. Change it later with `set_model_config`."""
    return _upload_model(
        weights_dir,
        serve_app,
        suffix,
        family,
        run_id,
        run_name,
        requirements,
        config,
    )


def import_model(
    source: str | Path | Callable[[], str | Path],
    serve_app: type,
    family: str,
    suffix: str,
    requirements: ModelRequirements | None = None,
    config: dict[str, str] | None = None,
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
        weights are kept (see `_refresh_bundle`). `requirements` and `config`
        are stored only if the version has none yet, so values changed since
        with `set_model_requirements` / `set_model_config` are kept. To replace
        the weights, `delete_model` it first.
      - "uploading": raises RuntimeError - another process is importing it.
      - "upload_failed" / "broken": deleted and imported again.

    The entry is linked to no run, so deleting a run leaves it in place. Deploy it like any saved model:
    `deploy_model(family, suffix, IMPORTED)`."""
    return _register_imported(
        source, serve_app, family, suffix, requirements, config
    )


def register_model(
    serve_app: type,
    family: str,
    suffix: str,
    requirements: ModelRequirements | None = None,
    config: dict[str, str] | None = None,
) -> SavedModel:
    """Register a model that stages no weights under the fixed key
    (family, suffix, IMPORTED), once.

    For a model whose bytes are not ours to hold: a serve-app that forwards
    requests to a hosted API, or one that reaches for the weights itself at
    startup. There is nothing to upload, so only `serve_app`'s bundle is
    stored and the version's source reads NO_WEIGHTS - `load_model` on such a
    model raises, and `SavedModel.has_weights` is False. What it needs instead
    of weights - which model a provider should be asked for, the name of the
    secret holding the key - belongs in `config`, which the serve-app reads
    with `model_config` at construction.

    Registration is otherwise `import_model`'s, down to the phase an already
    registered version leaves it in ("ready" is reused and its bundle
    refreshed, "uploading" raises, "upload_failed"/"broken" is replaced), so
    the entry is indistinguishable from an imported one to `deploy_model`,
    `list_models` and the dashboard."""
    return _register_imported(
        None, serve_app, family, suffix, requirements, config
    )


def _register_imported(
    weights: str | Path | Callable[[], str | Path] | None,
    serve_app: type,
    family: str,
    suffix: str,
    requirements: ModelRequirements | None,
    config: dict[str, str] | None,
) -> SavedModel:
    """Register `weights` under (family, suffix, IMPORTED) unless a version is
    already there - the once-only registration `import_model` and
    `register_model` share; `weights` is None for the model that stages
    none."""
    existing = model_registry_status(family, suffix, IMPORTED)
    if existing is not None:
        if existing.phase == _PHASE_READY:
            _refresh_bundle(serve_app, family, suffix)
            if requirements is not None:
                existing.requirements = _set_missing_requirements(
                    family, suffix, requirements
                )
            if config is not None:
                existing.config = _set_missing_config(family, suffix, config)
            return existing
        if existing.phase == _PHASE_UPLOADING:
            raise RuntimeError(
                f"Model {family}/{suffix}/{IMPORTED} is being imported by "
                "another process"
            )
        delete_model(family, suffix, IMPORTED)
    return _upload_model(
        weights, serve_app, suffix, family, None, IMPORTED, requirements, config
    )


def _refresh_bundle(serve_app: type, family: str, suffix: str) -> None:
    """Re-bundle an imported model's serve-app when its fingerprint differs
    from the bundle stored on the version, leaving the weights in place.

    The new bundle is uploaded next to the old one and the entry's tags are
    pointed at it, so the next `deploy_model` runs the new code. An app that is
    already running keeps the code it started with until it is deployed again;
    the old bundle stays in storage so that app can still restart."""
    version = _get_version(family, suffix, IMPORTED)
    serve_bundle = build_bundle(serve_app)
    if metadata_from_tags(version.tags).fingerprint == serve_bundle.fingerprint:
        return
    meta = upload_bundle(serve_bundle, family, suffix, IMPORTED)
    _set_tags(family, suffix, IMPORTED, metadata_to_tags(meta))


def _set_missing_requirements(
    family: str, suffix: str, requirements: ModelRequirements
) -> ModelRequirements:
    """Store `requirements` on an imported model whose version has none yet.
    Returns the requirements the version holds afterwards."""
    version = _get_version(family, suffix, IMPORTED)
    if has_requirement_tags(version.tags):
        return requirements_from_tags(version.tags)
    _set_tags(family, suffix, IMPORTED, requirements_to_tags(requirements))
    return requirements


def _set_missing_config(
    family: str, suffix: str, config: dict[str, str]
) -> dict[str, str]:
    """Store `config` on an imported model whose version has none yet. Returns
    the config the version holds afterwards."""
    version = _get_version(family, suffix, IMPORTED)
    if _CONFIG_TAG in version.tags:
        return _config_from_tags(version.tags)
    _set_tags(family, suffix, IMPORTED, {_CONFIG_TAG: _config_to_tag(config)})
    return config


def _upload_model(
    weights: str | Path | Callable[[], str | Path] | None,
    serve_app: type,
    suffix: str,
    family: str,
    run_id: str | None,
    run_name: str,
    requirements: ModelRequirements | None,
    config: dict[str, str] | None,
) -> SavedModel:
    """Register an entry in "uploading", resolve `weights` to a directory
    (calling it when it is a callable), upload the weights and the serve-app
    bundle, and flip it to "ready" (or "upload_failed").

    `weights` is None for a model that stages none: its source reads
    NO_WEIGHTS and the bundle is the only thing uploaded. The phases are the
    same either way, so one registration path covers both."""
    prefix = f"models/{run_name}/{family}/{suffix}"
    source = (
        NO_WEIGHTS
        if weights is None
        else f"s3://{get_s3_bucket()}/{prefix}/weights/"
    )
    # Register the entry up front in the "uploading" phase, before `weights`
    # is resolved: the dashboard surfaces the model while it is still being
    # fetched and uploaded, and a concurrent `import_model` sees the import in
    # flight for the whole download instead of starting one of its own. The
    # size is stamped once the directory exists; the bundle tags and the flip
    # to "ready" happen only after the upload lands. A weights-less
    # registration has only its bundle to upload, and passes through the same
    # phases. No requirements and no
    # config leave their tags unset, so a later `import_model` can still store
    # them. An entry already under the key is replaced: saving twice in one
    # run leaves the second save.
    state.put(
        "models",
        family,
        suffix,
        run_name,
        body={
            "run_id": run_id,
            "source": source,
            "tags": {
                "family": family,
                "suffix": suffix,
                "run_name": run_name,
                _LIFECYCLE_TAG: _PHASE_UPLOADING,
                **(requirements_to_tags(requirements) if requirements is not None else {}),
                **({_CONFIG_TAG: _config_to_tag(config)} if config is not None else {}),
            },
        },
    )
    try:
        if weights is not None:
            weights_dir = weights() if callable(weights) else weights
            _set_tags(
                family, suffix, run_name, {"size_bytes": str(_dir_size_bytes(weights_dir))}
            )
            s3_util.upload_dir(str(weights_dir), dest_path=f"{prefix}/weights")
        bundle_meta = bundle_class(serve_app, family, suffix, run_name)
        # One write, so the entry reads "ready" only with its bundle tags in place.
        _set_tags(
            family,
            suffix,
            run_name,
            {**metadata_to_tags(bundle_meta), _LIFECYCLE_TAG: _PHASE_READY},
        )
    except Exception:
        _set_tags(family, suffix, run_name, {_LIFECYCLE_TAG: _PHASE_UPLOAD_FAILED})
        raise
    return _to_saved_model(_get_version(family, suffix, run_name))


def load_model(family: str, suffix: str, run_name: str) -> Path:
    """Download a saved model's weights to a local directory and return its Path.

    cortexgrid moves an opaque directory of bytes and never interprets its
    contents; the serve-app reconstructs the model from it however it likes
    (`from_pretrained`, `torch.load`, ...). The returned directory persists
    after this call - the caller (typically a serve-app loading weights at
    startup) owns its lifetime.

    Raises ValueError for a model registered with `register_model`: it stages
    no weights, so there is none to hand back."""
    version = _get_version(family, suffix, run_name)
    if version is None or not version.source:
        raise ValueError(f"No model {family}/{suffix}/{run_name}")
    if version.source == NO_WEIGHTS:
        raise ValueError(
            f"Model {family}/{suffix}/{run_name} was registered without "
            "weights; there is nothing to load"
        )
    return _download_s3_uri(version.source, None)


def list_models() -> list[SavedModel]:
    """Return SavedModel records for every entry in the registry, including
    entries still uploading or whose upload failed (see `SavedModel.phase`)."""
    return [_to_saved_model(_ModelVersion(**record)) for record in state.get("models")]


def model_registry_status(
    family: str, suffix: str, run_name: str
) -> SavedModel | None:
    """Report the registry lifecycle of one model, or None if it was never
    registered (no upload ever started).

    The registry lifecycle is owned here: it begins when `save_model` creates
    the entry (`phase="uploading"`), becomes `"ready"` once the weights
    and serve bundle finish uploading, `"upload_failed"` if the upload errored,
    or `"broken"` if an upload has stayed in progress past _UPLOAD_DEADLINE
    (the writer is presumed dead). Serving is a separate lifecycle; see
    `cortexgrid.model_serving.model_serving_status`.
    """
    version = _get_version(family, suffix, run_name)
    return _to_saved_model(version) if version is not None else None


def set_model_requirements(
    family: str, suffix: str, run_name: str, requirements: ModelRequirements
) -> None:
    """Replace the hardware requirements stored on a model. Takes effect on
    its next `deploy_model`; a replica already running keeps its placement.
    Raises ValueError if the model was never registered."""
    if not _set_tags(family, suffix, run_name, requirements_to_tags(requirements)):
        raise ValueError(f"No model {family}/{suffix}/{run_name}")


def model_config(family: str, suffix: str, run_name: str) -> dict[str, str]:
    """The config mapping stored on a model, empty if it has none.

    Meant for the serve-app to call in `__init__` with the
    (family, suffix, run_name) it was constructed with: the settings that are
    not the weights - a provider's model id, an endpoint, the name of a secret
    to read - travel with the registry entry instead of the bundled code, so
    changing one is an edit on the model card rather than a re-save.

    Read at construction, so a replica keeps the values it started with until
    it is deployed again. Raises ValueError if the model was never
    registered."""
    version = _get_version(family, suffix, run_name)
    if version is None:
        raise ValueError(f"No model {family}/{suffix}/{run_name}")
    return _config_from_tags(version.tags)


def set_model_config(
    family: str, suffix: str, run_name: str, config: dict[str, str]
) -> None:
    """Replace the config mapping stored on a model - the whole mapping, so a
    key left out of `config` is gone. Takes effect on its next `deploy_model`;
    a replica already running keeps the values it read at construction.
    Raises ValueError if the model was never registered."""
    if not _set_tags(family, suffix, run_name, {_CONFIG_TAG: _config_to_tag(config)}):
        raise ValueError(f"No model {family}/{suffix}/{run_name}")


def delete_model(family: str, suffix: str, run_name: str) -> None:
    """Delete the registry entry, its weights blob, and its serve bundle."""
    state.delete("models", family, suffix, run_name)
    s3_util.delete_prefix(f"models/{run_name}/{family}/{suffix}/")
    s3_util.delete_prefix(f"serve-bundles/{run_name}/{family}__{suffix}")


def delete_models_for_run(run_id: str) -> None:
    """Delete every registry entry produced by a run, plus its blobs and serve bundles."""
    run_name = state.get("runs", run_id)["run_name"]
    state.delete("models", params={"run_id": run_id})
    s3_util.delete_prefix(f"models/{run_name}/")
    s3_util.delete_prefix(f"serve-bundles/{run_name}/")
