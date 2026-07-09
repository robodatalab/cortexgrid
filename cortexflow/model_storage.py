"""Model registry backed by MLflow Model Registry; weights stored directly in S3.

Mapping cortexflow taxonomy <-> MLflow Registry:
    family + suffix     -> RegisteredModel.name = "<family>/<suffix>"
    run_name            -> ModelVersion.tags["run_name"]
    family, suffix      -> ModelVersion.tags["family"], ["suffix"]   (denormalized)
    weights blob path   -> ModelVersion.source =
                           "s3://<bucket>/models/<run_name>/<family>/<suffix>/weights/"
    run linkage         -> ModelVersion.run_id  (built-in MLflow field)

storage.py is pure: it takes run_id/run_name as explicit args and never reads
the active Experiment singleton. The facade that fills those in lives in
cortexflow/__init__.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Any

from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from cortexflow import s3_util
from cortexflow.infra import get_mlflow_tracking_uri, get_s3_bucket
from cortexflow.model_serving import (
    bundle_class,
    metadata_to_tags,
)


@dataclass
class SavedModel:
    family: str
    suffix: str
    run_name: str
    created_at: str
    data_blob_path: str
    size_bytes: int


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
) -> SavedModel:
    """Upload a weights directory to S3 and register a new MLflow ModelVersion
    paired with the serve-app that fronts it.

    cortexflow stores the weights as an opaque directory: it never inspects,
    serializes, or reconstructs their contents, so the on-disk format
    (HuggingFace `save_pretrained`, `torch.save`, ONNX, anything) is entirely
    the caller's concern. That directory boundary is the open-closed extension
    point - new model kinds need no change here.

    `serve_app` is the Ray Serve ingress class that will front these weights.
    Its code is bundled and its import path, bundle URL, and pip list are
    stored as tags on the ModelVersion so `deploy_model` can bind it later
    without the caller holding the class object."""
    bucket = get_s3_bucket()
    prefix = f"models/{run_name}/{family}/{suffix}"
    size_bytes = _dir_size_bytes(weights_dir)
    s3_util.upload_dir(str(weights_dir), dest_path=f"{prefix}/weights")
    source = f"s3://{bucket}/{prefix}/weights/"
    bundle_meta = bundle_class(serve_app, family, suffix, run_name)
    name = f"{family}__{suffix}"
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    _ensure_registered_model(client, name)
    version = client.create_model_version(
        name=name,
        source=source,
        run_id=run_id,
        tags={
            "family": family,
            "suffix": suffix,
            "run_name": run_name,
            "size_bytes": str(size_bytes),
            **metadata_to_tags(bundle_meta),
        },
    )
    return _to_saved_model(version)


def load_model(family: str, suffix: str, run_name: str) -> Path:
    """Download a saved model's weights to a local directory and return its Path.

    cortexflow moves an opaque directory of bytes and never interprets its
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
    """Return SavedModel records for every ModelVersion in the registry."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    return [_to_saved_model(v) for v in client.search_model_versions("")]


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
