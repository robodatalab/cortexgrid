"""Durable checkpointing for cortexflow jobs.

Save arbitrary state (primitives, torch tensors, state_dicts) via MLflow
artifacts and resume from the latest checkpoint on retry.

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
import tempfile
from pathlib import Path
from typing import Any

import cloudpickle  # type: ignore
import torch
from mlflow.tracking import MlflowClient

from cortexflow import s3_util
from cortexflow.experiment import Experiment, get_mlflow_tracking_uri

log = logging.getLogger(__name__)
_CORTEXFLOW_JOB_ID: str | None = None


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
    """Attribute-based checkpoint persisted via MLflow artifacts.

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

    _INTERNAL = frozenset(("_prefix", "_data"))

    def __init__(self, prefix: str, *, _data: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "_prefix", prefix)
        object.__setattr__(self, "_data", _data if _data is not None else {})

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._INTERNAL:
            object.__setattr__(self, name, value)
        else:
            self._data[name] = value

    def __getattr__(self, name: str) -> Any:
        if name in ("_prefix", "_data", "_INTERNAL"):
            return object.__getattribute__(self, name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Checkpoint has no attribute {name!r}")

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:
        keys = ", ".join(sorted(self._data))
        return f"Checkpoint(prefix={self._prefix!r}, attrs=[{keys}])"

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
        """Upload attr blobs to MinIO; log manifest.json via MLflow."""
        exp = Experiment.get_instance()
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        tmpdir = Path(tempfile.mkdtemp())

        manifest: dict[str, Any] = {"attrs": {}}

        for name, value in self._data.items():
            data, fmt = _serialize(value)
            filename = f"{name}{_ext_for(fmt)}"
            (tmpdir / filename).write_bytes(data)
            uri = s3_util.upload(
                str(tmpdir / filename), dest_path=f"{self._prefix}/{filename}"
            )
            manifest["attrs"][name] = {"uri": uri, "format": fmt}

        (tmpdir / "manifest.json").write_text(json.dumps(manifest))
        client.log_artifact(
            exp.run_id, str(tmpdir / "manifest.json"), artifact_path=self._prefix
        )
        log.info("Checkpoint saved: %s (%d attrs)", self._prefix, len(self._data))

    @classmethod
    def _load(cls, prefix: str) -> Checkpoint | None:
        """Download manifest via MLflow; download attr blobs from MinIO."""
        exp = Experiment.get_instance()
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())

        try:
            manifest_path = client.download_artifacts(
                exp.run_id, f"{prefix}/manifest.json"
            )
            manifest = json.loads(Path(manifest_path).read_text())
        except Exception:
            return None

        data: dict[str, Any] = {}
        tmpdir = Path(tempfile.mkdtemp())
        for name, info in manifest["attrs"].items():
            try:
                bucket, _, key = info["uri"].removeprefix("s3://").partition("/")
                file_path = s3_util.download(bucket, key, str(tmpdir / Path(key).name))
                raw = Path(file_path).read_bytes()
                data[name] = _deserialize(raw, info["format"])
            except Exception:
                log.warning("Failed to load checkpoint attribute %r", name)
                return None

        log.info("Checkpoint loaded: %s (%d attrs)", prefix, len(data))
        return cls(prefix, _data=data)


def set_cortexflow_job_id(job_id: str) -> None:
    global _CORTEXFLOW_JOB_ID
    _CORTEXFLOW_JOB_ID = job_id


def get_cortexflow_job_id() -> str | None:
    """Return the current job ID, or None if not running inside a job."""
    global _CORTEXFLOW_JOB_ID
    return _CORTEXFLOW_JOB_ID


def _checkpoint_prefix() -> str:
    job_id = get_cortexflow_job_id() or "global"
    return f"checkpoint/{job_id}"


def checkpoint() -> Checkpoint:
    """Create a checkpoint for the current job. Use as a context manager.

    Returns a no-op checkpoint if not running inside a cortexflow job,
    so callers don't need to guard with ``if`` checks.

    Example::

        with cortexflow.checkpoint() as ckpt:
            ckpt.epoch = epoch
            ckpt.save_training_state(model, optimizer, scheduler)
    """
    prefix = _checkpoint_prefix()
    return Checkpoint(prefix)


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
    prefix = _checkpoint_prefix()
    return Checkpoint._load(prefix)
