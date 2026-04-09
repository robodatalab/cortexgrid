"""Durable checkpointing for cortexflow jobs.

Save arbitrary state (primitives, torch tensors, state_dicts) to MinIO
and resume from the latest checkpoint on retry.

Usage (save)::

    with cortexflow.checkpoint() as ckpt:
        ckpt.epoch = epoch
        ckpt.global_step = step
        ckpt.save_training_state(model, optimizer, scheduler)

Usage (resume)::

    ckpt = cortexflow.resume()
    if ckpt:
        ckpt.restore_training_state(model, optimizer, scheduler)
        start_epoch = ckpt.epoch + 1
"""

from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

import cloudpickle  # type: ignore
import torch

from cortexflow.s3_util import get_s3_client

log = logging.getLogger(__name__)

CHECKPOINT_BUCKET = "cortexflow-checkpoints"


def _is_torch_serializable(value: Any) -> bool:
    """True if the value should be serialized with torch.save."""
    if isinstance(value, torch.Tensor):
        return True
    if isinstance(value, dict) and value:
        return any(isinstance(v, torch.Tensor) for v in value.values())
    return False


def _serialize(value: Any) -> tuple[bytes, str]:
    """Serialize a value. Returns (bytes, format_name)."""
    if _is_torch_serializable(value):
        buf = io.BytesIO()
        torch.save(value, buf)
        return buf.getvalue(), "torch"
    return cloudpickle.dumps(value), "cloudpickle"


def _deserialize(data: bytes, fmt: str) -> Any:
    """Deserialize bytes using the given format."""
    if fmt == "torch":
        buf = io.BytesIO(data)
        return torch.load(buf, map_location="cpu", weights_only=False)
    return cloudpickle.loads(data)


def _ext_for(fmt: str) -> str:
    return ".pt" if fmt == "torch" else ".pkl"


class Checkpoint:
    """Attribute-based checkpoint persisted to MinIO.

    Assign any cloudpickle-compatible or torch-serializable value to an
    attribute and it will be saved when the context manager exits::

        with cortexflow.checkpoint() as ckpt:
            ckpt.epoch = 5
            ckpt.model_state = model.state_dict()

    Read values back after calling ``cortexflow.resume()``::

        ckpt = cortexflow.resume()
        if ckpt:
            model.load_state_dict(ckpt.model_state)
    """

    _INTERNAL = frozenset(("_job_id", "_data"))

    def __init__(self, job_id: str, *, _data: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "_job_id", job_id)
        object.__setattr__(self, "_data", _data if _data is not None else {})

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._INTERNAL:
            object.__setattr__(self, name, value)
        else:
            self._data[name] = value

    def __getattr__(self, name: str) -> Any:
        if name in ("_job_id", "_data", "_INTERNAL"):
            return object.__getattribute__(self, name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Checkpoint has no attribute {name!r}")

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:
        keys = ", ".join(sorted(self._data))
        return f"Checkpoint(job_id={self._job_id!r}, attrs=[{keys}])"

    def save_training_state(
        self,
        model: Any,
        optimizer: Any,
        scheduler: Any | None = None,
    ) -> None:
        """Save model, optimizer, and optionally scheduler state_dicts."""
        self._data["_model_state"] = model.state_dict()
        self._data["_optimizer_state"] = optimizer.state_dict()
        if scheduler is not None:
            self._data["_scheduler_state"] = scheduler.state_dict()

    def restore_training_state(
        self,
        model: Any,
        optimizer: Any,
        scheduler: Any | None = None,
    ) -> None:
        """Load state_dicts back into existing model/optimizer/scheduler."""
        model.load_state_dict(self._data["_model_state"])
        optimizer.load_state_dict(self._data["_optimizer_state"])
        if scheduler is not None and "_scheduler_state" in self._data:
            scheduler.load_state_dict(self._data["_scheduler_state"])

    def __enter__(self) -> Checkpoint:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            self._persist()

    def _persist(self) -> None:
        """Serialize each attribute and upload to MinIO."""
        client = get_s3_client()
        prefix = self._job_id

        _ensure_bucket(client, CHECKPOINT_BUCKET)

        manifest: dict[str, Any] = {"attrs": {}}

        for name, value in self._data.items():
            data, fmt = _serialize(value)
            filename = f"{name}{_ext_for(fmt)}"
            key = f"{prefix}/{filename}"
            client.put_object(
                Bucket=CHECKPOINT_BUCKET,
                Key=key,
                Body=data,
            )
            manifest["attrs"][name] = {"file": filename, "format": fmt}

        manifest_key = f"{prefix}/manifest.json"
        client.put_object(
            Bucket=CHECKPOINT_BUCKET,
            Key=manifest_key,
            Body=json.dumps(manifest).encode(),
        )
        log.info("Checkpoint saved: %s (%d attrs)", self._job_id, len(self._data))

    @classmethod
    def _load(cls, job_id: str) -> Checkpoint | None:
        """Download and deserialize a checkpoint from MinIO. Returns None if not found."""
        client = get_s3_client()
        manifest_key = f"{job_id}/manifest.json"

        try:
            resp = client.get_object(Bucket=CHECKPOINT_BUCKET, Key=manifest_key)
            manifest = json.loads(resp["Body"].read())
        except client.exceptions.NoSuchKey:
            return None
        except Exception:
            return None

        data: dict[str, Any] = {}
        for name, info in manifest["attrs"].items():
            key = f"{job_id}/{info['file']}"
            try:
                resp = client.get_object(Bucket=CHECKPOINT_BUCKET, Key=key)
                raw = resp["Body"].read()
                data[name] = _deserialize(raw, info["format"])
            except Exception:
                log.warning("Failed to load checkpoint attribute %r, skipping", name)
                return None

        log.info("Checkpoint loaded: %s (%d attrs)", job_id, len(data))
        return cls(job_id, _data=data)


def _ensure_bucket(client: Any, bucket: str) -> None:
    """Create the bucket if it doesn't exist."""
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.NoSuchBucket:
        client.create_bucket(Bucket=bucket)
    except Exception:
        try:
            client.create_bucket(Bucket=bucket)
        except Exception:
            pass


def get_job_id() -> str:
    """Return the current CORTEXFLOW_JOB_ID. Raises if not set."""
    job_id = os.environ.get("CORTEXFLOW_JOB_ID")
    if not job_id:
        raise RuntimeError(
            "CORTEXFLOW_JOB_ID not set. Are you inside a cortexflow job?"
        )
    return job_id


class _NoOpCheckpoint:
    """Checkpoint that silently discards all writes. Used outside cortexflow jobs."""

    def __setattr__(self, name: str, value: Any) -> None:
        pass

    def __enter__(self) -> _NoOpCheckpoint:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass

    def save_training_state(self, *args: Any, **kwargs: Any) -> None:
        pass


def checkpoint() -> Checkpoint | _NoOpCheckpoint:
    """Create a checkpoint for the current job. Use as a context manager.

    Returns a no-op checkpoint if not running inside a cortexflow job,
    so callers don't need to guard with ``if`` checks.

    Example::

        with cortexflow.checkpoint() as ckpt:
            ckpt.epoch = epoch
            ckpt.save_training_state(model, optimizer, scheduler)
    """
    job_id = os.environ.get("CORTEXFLOW_JOB_ID")
    if not job_id:
        return _NoOpCheckpoint()
    return Checkpoint(job_id)


def resume() -> Checkpoint | None:
    """Load the latest checkpoint for the current job, or None.

    Returns None if not running inside a cortexflow job or if no
    checkpoint exists.

    Example::

        ckpt = cortexflow.resume()
        if ckpt:
            ckpt.restore_training_state(model, optimizer, scheduler)
            start_epoch = ckpt.epoch + 1
    """
    job_id = os.environ.get("CORTEXFLOW_JOB_ID")
    if not job_id:
        return None
    return Checkpoint._load(job_id)
