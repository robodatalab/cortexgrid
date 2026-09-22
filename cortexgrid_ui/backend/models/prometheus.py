from __future__ import annotations

import os
from typing import Any

import requests  # type: ignore


_PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090",
)
_QUERY_TIMEOUT_S = 30


def query_instant(promql: str) -> list[dict[str, Any]]:
    return _query("query", {"query": promql})


def query_range(
    promql: str, start_s: float, end_s: float, step_s: float
) -> list[dict[str, Any]]:
    return _query(
        "query_range",
        {"query": promql, "start": start_s, "end": end_s, "step": step_s},
    )


def _query(endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    response = requests.get(
        f"{_PROMETHEUS_URL}/api/v1/{endpoint}",
        params=params,
        timeout=_QUERY_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()["data"]["result"]
