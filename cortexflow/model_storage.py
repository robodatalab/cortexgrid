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

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Any

from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from cortexflow import s3_util
from cortexflow.infra import get_mlflow_tracking_uri, get_s3_bucket


@dataclass
class SavedModel:
    family: str
    suffix: str
    run_name: str
    created_at: str
    data_blob_path: str


def _to_saved_model(version: Any) -> SavedModel:
    return SavedModel(
        family=version.tags["family"],
        suffix=version.tags["suffix"],
        run_name=version.tags["run_name"],
        created_at=datetime.fromtimestamp(
            version.creation_timestamp / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        data_blob_path=version.source,
    )


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
    model_dir: str | Path,
    suffix: str,
    family: str,
    run_id: str,
    run_name: str,
) -> SavedModel:
    """Upload weights to S3 and register a new MLflow ModelVersion."""
    bucket = get_s3_bucket()
    prefix = f"models/{run_name}/{family}/{suffix}"
    s3_util.upload_dir(str(model_dir), dest_path=f"{prefix}/weights")
    source = f"s3://{bucket}/{prefix}/weights/"
    name = f"{family}__{suffix}"
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    _ensure_registered_model(client, name)
    version = client.create_model_version(
        name=name,
        source=source,
        run_id=run_id,
        tags={"family": family, "suffix": suffix, "run_name": run_name},
    )
    return _to_saved_model(version)


def load_model(
    family: str,
    suffix: str,
    run_name: str,
    dest_dir: str | Path | None = None,
) -> Path:
    """Find the ModelVersion by (family, suffix, run_name) and download weights."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    name = f"{family}__{suffix}"
    versions = client.search_model_versions(
        f"name='{name}' and tags.run_name='{run_name}'"
    )
    if not versions or not versions[0].source:
        raise ValueError(f"No model {family}/{suffix}/{run_name}")
    return _download_s3_uri(versions[0].source, dest_dir)


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
