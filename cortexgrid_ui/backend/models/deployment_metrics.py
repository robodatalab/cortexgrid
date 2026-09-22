from __future__ import annotations

import re

from pydantic import BaseModel

from cortexgrid_ui.backend.models.prometheus import query_range


_WINDOW_S = 3600.0
_STEP_S = 30.0
_BYTES_PER_MIB = 1024 * 1024

TimeSeries = list[tuple[float, float]]


class DeploymentMetrics(BaseModel):
    requests_per_second: TimeSeries
    succeeded_responses_per_second: TimeSeries
    failed_responses_per_second: TimeSeries
    cpu_percent: TimeSeries
    memory_bytes: TimeSeries
    gpu_percent: TimeSeries
    gpu_memory_bytes: TimeSeries


def read_deployment_metrics(application: str, now_s: float) -> DeploymentMetrics:
    return DeploymentMetrics(
        **{
            name: _read_series(promql, now_s)
            for name, promql in _promql_by_metric(application).items()
        }
    )


def _promql_by_metric(application: str) -> dict[str, str]:
    of_application = f'application="{application}"'
    of_replicas = (
        f'Component=~"ray::ServeReplica:{_as_promql_regex(application)}:.*"'
    )
    return {
        "requests_per_second": (
            f"sum(rate(ray_serve_num_router_requests_total{{{of_application}}}[1m]))"
        ),
        "succeeded_responses_per_second": (
            "sum(rate(ray_serve_num_http_requests_total"
            f'{{{of_application},status_code=~"2.."}}[1m]))'
        ),
        "failed_responses_per_second": (
            "sum(rate(ray_serve_num_http_requests_total"
            f'{{{of_application},status_code!~"2.."}}[1m]))'
        ),
        "cpu_percent": f"sum(ray_component_cpu_percentage{{{of_replicas}}})",
        "memory_bytes": f"sum(ray_component_uss_bytes{{{of_replicas}}})",
        "gpu_percent": f"sum(ray_component_gpu_percentage{{{of_replicas}}})",
        "gpu_memory_bytes": (
            f"sum(ray_component_gpu_memory_mb{{{of_replicas}}}) * {_BYTES_PER_MIB}"
        ),
    }


def _as_promql_regex(text: str) -> str:
    return re.escape(text).replace("\\", "\\\\")


def _read_series(promql: str, now_s: float) -> TimeSeries:
    results = query_range(promql, now_s - _WINDOW_S, now_s, _STEP_S)
    if not results:
        return []
    return [(float(timestamp), float(value)) for timestamp, value in results[0]["values"]]
