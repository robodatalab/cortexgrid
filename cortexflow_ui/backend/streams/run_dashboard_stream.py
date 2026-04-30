"""Per-run dashboard stream.

Emits per-item diff events for params, metrics, and the MLflow run URL.
Items are keyed by ``param:<name>``, ``metric:<name>``, ``url``.
Each metric carries its full history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cortexflow.experiment import get_mlflow_tracking_uri
from cortexflow.infra import get_mlflow_run_url
from cortexflow.mlflow_util import get_metric_history
from cortexflow_ui.backend.streams.config import RUN_DASHBOARD_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.streams.experiments_stream import resolve_run_id
from cortexflow_ui.backend.utils.keyed_stream import KeyedStream
from mlflow.tracking import MlflowClient


@dataclass
class Param:
    id: str
    run_name: str
    name: str
    value: str


@dataclass
class Metric:
    id: str
    run_name: str
    name: str
    history: list[dict[str, Any]]


@dataclass
class Url:
    id: str
    run_name: str
    url: str


DashboardItem = Param | Metric | Url


RunName = str
DashboardItemId = str


def poll_run_dashboard(run_name: RunName) -> dict[DashboardItemId, DashboardItem]:
    run_id = resolve_run_id(run_name)
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    run = client.get_run(run_id)
    items: dict[DashboardItemId, DashboardItem] = {}
    for name, value in run.data.params.items():
        item_id = f"param:{name}"
        items[item_id] = Param(id=item_id, run_name=run_name, name=name, value=value)
    for name in run.data.metrics.keys():
        item_id = f"metric:{name}"
        items[item_id] = Metric(
            id=item_id,
            run_name=run_name,
            name=name,
            history=get_metric_history(run_id, name),
        )
    items["url"] = Url(id="url", run_name=run_name, url=get_mlflow_run_url(run_id))
    return items


stream: KeyedStream[RunName, DashboardItemId, DashboardItem] = KeyedStream(
    name="run_dashboard_stream",
    poll_fn=poll_run_dashboard,
    poll_interval_sec=RUN_DASHBOARD_STREAM_POLL_INTERVAL_SEC,
)
