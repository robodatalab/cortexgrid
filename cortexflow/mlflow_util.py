"""MLflow wrappers.

Thin layer that configures MLflow tracking URI and S3 credentials,
then exposes convenience functions for common operations.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Any, Generator

import mlflow
import mlflow.tracking

from cortexflow.config import get_config


def _ensure_configured() -> None:
    config = get_config()
    if config.mlflow_tracking_uri:
        mlflow.set_tracking_uri(config.mlflow_tracking_uri)
    if config.mlflow_s3_endpoint_url:
        os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", config.mlflow_s3_endpoint_url)
    if config.s3_access_key:
        os.environ.setdefault("AWS_ACCESS_KEY_ID", config.s3_access_key)
    if config.s3_secret_key:
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", config.s3_secret_key)


@contextmanager
def mlflow_run(
    experiment: str,
    run_name: str | None = None,
    run_id: str | None = None,
    tags: dict[str, str] | None = None,
) -> Generator[mlflow.ActiveRun, None, None]:
    """Context manager for an MLflow run with auto-configured tracking.

    Usage:
        with cortexflow.mlflow_run("my-experiment", run_name="v3") as run:
            cortexflow.log_metric("loss", 0.5, step=1)
    """
    _ensure_configured()
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_id=run_id, run_name=run_name, tags=tags) as run:
        yield run


def log_metric(key: str, value: float, step: int | None = None) -> None:
    """Log a metric to the current active MLflow run."""
    mlflow.log_metric(key, value, step=step)


def log_metrics(metrics: dict[str, float], step: int | None = None) -> None:
    """Log multiple metrics to the current active MLflow run."""
    mlflow.log_metrics(metrics, step=step)


def log_params(params: dict[str, Any]) -> None:
    """Log parameters to the current active MLflow run."""
    mlflow.log_params(params)


def log_artifact(local_path: str, artifact_path: str | None = None) -> None:
    """Log a file as an artifact to the current active MLflow run."""
    mlflow.log_artifact(local_path, artifact_path=artifact_path)


def save_checkpoint(
    model: Any,
    optimizer: Any | None = None,
    epoch: int = 0,
    extra: dict[str, Any] | None = None,
) -> str:
    """Save a training checkpoint to the current MLflow run's artifacts.

    Args:
        model: PyTorch model (or any object with state_dict()).
        optimizer: Optional optimizer with state_dict().
        epoch: Current epoch number.
        extra: Additional data to include in the checkpoint.

    Returns:
        The artifact path of the saved checkpoint.
    """
    import torch

    state: dict[str, Any] = {
        "epoch": epoch,
        "model_state_dict": model.state_dict(),
    }
    if optimizer is not None:
        state["optimizer_state_dict"] = optimizer.state_dict()
    if extra:
        state.update(extra)

    artifact_path = f"checkpoints/epoch_{epoch}"

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "checkpoint.pt")
        torch.save(state, path)
        mlflow.log_artifact(path, artifact_path=artifact_path)

    return artifact_path


def load_checkpoint(
    run_id: str,
    epoch: int | None = None,
) -> dict[str, Any]:
    """Load a checkpoint from an MLflow run's artifacts.

    Args:
        run_id: MLflow run ID to load from.
        epoch: Specific epoch to load. If None, loads the latest.

    Returns:
        Dict with 'epoch', 'model_state_dict', 'optimizer_state_dict' (if saved), etc.
    """
    import torch

    _ensure_configured()
    client = mlflow.tracking.MlflowClient()

    artifacts = client.list_artifacts(run_id, "checkpoints")
    if not artifacts:
        raise FileNotFoundError(f"No checkpoints found for run {run_id}")

    if epoch is not None:
        artifact_path = f"checkpoints/epoch_{epoch}/checkpoint.pt"
    else:
        latest = sorted(artifacts, key=lambda a: a.path)[-1]
        artifact_path = f"{latest.path}/checkpoint.pt"

    local_path = mlflow.artifacts.download_artifacts(
        run_id=run_id,
        artifact_path=artifact_path,
    )

    return torch.load(local_path, map_location="cpu")


def get_mlflow_client() -> mlflow.tracking.MlflowClient:
    """Return a configured MlflowClient."""
    _ensure_configured()
    return mlflow.tracking.MlflowClient()
