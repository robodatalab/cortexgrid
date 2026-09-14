"""MLflow wrappers.

Thin layer that configures MLflow tracking URI and S3 credentials,
then exposes convenience functions for common operations.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from cortexgrid.experiment import Experiment
from cortexgrid.infra import get_mlflow_tracking_uri
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

    When `max_points` is given, MLflow samples server-side and returns at
    most that many points (saves DB work and bytes-on-the-wire). Without
    `max_points`, the full history is returned.
    """
    if max_points is not None:
        url = (
            f"{get_mlflow_tracking_uri().rstrip('/')}"
            "/ajax-api/2.0/mlflow/metrics/get-history-bulk-interval"
        )
        response = requests.get(
            url,
            params={"run_ids": run_id, "metric_key": key, "max_results": max_points},
        )
        response.raise_for_status()
        return [
            {"step": int(m["step"]), "value": float(m["value"]), "timestamp": int(m["timestamp"])}
            for m in response.json().get("metrics", [])
        ]
    client = get_mlflow_client()
    history = client.get_metric_history(run_id, key)
    return [
        {"step": m.step, "value": m.value, "timestamp": m.timestamp} for m in history
    ]


def list_run_params(run_id: str) -> dict[str, str]:
    """Return all parameter key/value pairs for a run."""
    client = get_mlflow_client()
    run = client.get_run(run_id)
    return dict(run.data.params)


def list_run_artifacts(run_id: str, path: str = "") -> list[str]:
    """Return artifact paths for a run."""
    client = get_mlflow_client()
    return [a.path for a in client.list_artifacts(run_id, path=path)]
