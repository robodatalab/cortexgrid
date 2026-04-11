"""MLflow wrappers.

Thin layer that configures MLflow tracking URI and S3 credentials,
then exposes convenience functions for common operations.
"""

from __future__ import annotations

import time
from typing import Any

from cortexflow.config import get_config
from haikunator import Haikunator  # type: ignore
from mlflow.entities import Metric
from mlflow.tracking import MlflowClient


def log_metric(key: str, value: float, step: int | None = None) -> None:
    """Log a metric to the current active MLflow run."""
    config = get_config()
    if config is None:
        raise ValueError("Call cortexflow.init to initialize")
    
    client = get_mlflow_client()
    client.log_metric(config.run_id, key, value, step=step)


def log_metrics(metrics: dict[str, float], step: int | None = None) -> None:
    """Log multiple metrics to the current active MLflow run."""
    config = get_config()
    if config is None:
        raise ValueError("Call cortexflow.init to initialize")
    
    client = get_mlflow_client()
    timestamp = int(time.time() * 1000)
    metric_entities = [
        Metric(key=k, value=v, timestamp=timestamp, step=step or 0)
        for k, v in metrics.items()
    ]
    client.log_batch(config.run_id, metrics=metric_entities)


def log_params(params: dict[str, Any]) -> None:
    """Log parameters to the current active MLflow run."""
    config = get_config()
    if config is None:
        raise ValueError("Call cortexflow.init to initialize")
    
    client = get_mlflow_client()
    for key, value in params.items():
        client.log_param(config.run_id, key, value)


def log_artifact(local_path: str, artifact_path: str | None = None) -> None:
    """Log a file as an artifact to the current active MLflow run."""
    config = get_config()
    if config is None:
        raise ValueError("Call cortexflow.init to initialize")
    
    client = get_mlflow_client()
    client.log_artifact(config.run_id, local_path, artifact_path=artifact_path)


def get_mlflow_client() -> MlflowClient:
    """Return a configured MlflowClient."""
    config = get_config()
    if config is None:
        raise ValueError("Call cortexflow.init to initialize")
    
    return MlflowClient(tracking_uri=config.mlflow_tracking_uri)


def get_experiment_list() -> dict[str, list[str]]:
    """Map MLflow experiment names to their run IDs."""
    client = get_mlflow_client()
    result: dict[str, list[str]] = {}
    for exp in client.search_experiments():
        runs = client.search_runs(experiment_ids=[exp.experiment_id])
        result[exp.name] = [run.info.run_id for run in runs]
    return result


def try_create_experiment_and_run(experiment: str | None) -> None:
    config = get_config()
    if config is None:
        raise ValueError("Call cortexflow.init to initialize")

    name_gen = Haikunator()
    if experiment is None:
        experiment = name_gen.haikunate(token_length=2, token_chars="0123456789")

    client = get_mlflow_client()
    experiment_obj = client.get_experiment_by_name(name=experiment)
    if experiment_obj:
        experiment_id = experiment_obj.experiment_id
    else:
        experiment_id = client.create_experiment(name=experiment)

    run_name = name_gen.haikunate(token_length=2, token_chars="0123456789")
    run = client.create_run(experiment_id=experiment_id, run_name=run_name)

    config.experiment_name = experiment
    config.run_id = run.info.run_id
