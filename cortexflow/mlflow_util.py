"""MLflow wrappers.

Thin layer that configures MLflow tracking URI and S3 credentials,
then exposes convenience functions for common operations.
"""

from __future__ import annotations

import time
from typing import Any

from cortexflow.experiment import Experiment
from cortexflow.infra import get_mlflow_tracking_uri
from mlflow.entities import Metric
from mlflow.tracking import MlflowClient


def log_metric(key: str, value: float, step: int | None = None) -> None:
    """Log a metric to the current active MLflow run."""
    experiment = Experiment.get_instance()

    client = get_mlflow_client()
    client.log_metric(experiment.run_id, key, value, step=step)


def log_metrics(metrics: dict[str, float], step: int | None = None) -> None:
    """Log multiple metrics to the current active MLflow run."""
    experiment = Experiment.get_instance()

    client = get_mlflow_client()
    timestamp = int(time.time() * 1000)
    metric_entities = [
        Metric(key=k, value=v, timestamp=timestamp, step=step or 0)
        for k, v in metrics.items()
    ]
    client.log_batch(experiment.run_id, metrics=metric_entities)


def log_params(params: dict[str, Any]) -> None:
    """Log parameters to the current active MLflow run."""
    experiment = Experiment.get_instance()

    client = get_mlflow_client()
    for key, value in params.items():
        client.log_param(experiment.run_id, key, value)


def log_artifact(local_path: str, artifact_path: str | None = None) -> None:
    """Log a file as an artifact to the current active MLflow run."""
    experiment = Experiment.get_instance()

    client = get_mlflow_client()
    client.log_artifact(experiment.run_id, local_path, artifact_path=artifact_path)


def get_mlflow_client() -> MlflowClient:
    """Return a configured MlflowClient."""
    return MlflowClient(tracking_uri=get_mlflow_tracking_uri())


def list_run_metrics(run_id: str) -> list[str]:
    """Return the metric key names logged for a run."""
    client = get_mlflow_client()
    run = client.get_run(run_id)
    return list(run.data.metrics.keys())


def get_metric_history(
    run_id: str, key: str, max_points: int | None = None
) -> list[dict[str, Any]]:
    """Return the history of a metric as [{step, value, timestamp}, ...].

    If `max_points` is given and the series is longer, downsample to
    that many evenly-spaced points; the first and last are always kept.
    """
    client = get_mlflow_client()
    history = client.get_metric_history(run_id, key)
    points = [
        {"step": m.step, "value": m.value, "timestamp": m.timestamp} for m in history
    ]
    if max_points is None or len(points) <= max_points:
        return points
    indices = [round(i * (len(points) - 1) / (max_points - 1)) for i in range(max_points)]
    return [points[i] for i in indices]


def list_run_params(run_id: str) -> dict[str, str]:
    """Return all parameter key/value pairs for a run."""
    client = get_mlflow_client()
    run = client.get_run(run_id)
    return dict(run.data.params)


def list_run_artifacts(run_id: str, path: str = "") -> list[str]:
    """Return artifact paths for a run."""
    client = get_mlflow_client()
    return [a.path for a in client.list_artifacts(run_id, path=path)]
