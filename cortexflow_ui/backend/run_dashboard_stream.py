"""Per-run dashboard stream.

Polls all RunDashboard fields (params, metric keys, metric histories,
artifacts, MLflow URL) every ``RUN_DASHBOARD_STREAM_POLL_INTERVAL_SEC``
seconds while at least one WebSocket subscriber is watching the run.
Pushes the full bundle on every poll. Per-metric histories use MLflow
server-side sampling capped at ``METRIC_MAX_POINTS``.
"""
from __future__ import annotations

from cortexflow.experiment import get_mlflow_tracking_uri
from cortexflow.infra import get_mlflow_run_url
from cortexflow.mlflow_util import get_metric_history, list_run_artifacts
from cortexflow_ui.backend.config import RUN_DASHBOARD_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.keyed_stream import KeyedStream
from mlflow.tracking import MlflowClient

METRIC_MAX_POINTS = 500


def poll_run_dashboard(run_id: str) -> dict:
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    run = client.get_run(run_id)
    metric_keys = list(run.data.metrics.keys())
    return {
        "params": dict(run.data.params),
        "metrics": {
            key: get_metric_history(run_id, key, max_points=METRIC_MAX_POINTS)
            for key in metric_keys
        },
        "artifacts": list_run_artifacts(run_id),
        "url": get_mlflow_run_url(run_id),
    }


stream = KeyedStream(
    name="run_dashboard_stream",
    poll_fn=poll_run_dashboard,
    poll_interval_sec=RUN_DASHBOARD_STREAM_POLL_INTERVAL_SEC,
)
