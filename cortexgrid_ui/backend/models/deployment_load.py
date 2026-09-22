from __future__ import annotations

import time
from dataclasses import dataclass, field

from cortexgrid_ui.backend.models.prometheus import query_instant


_REQUESTS_PER_SECOND_BY_APPLICATION = (
    "sum by (application) (rate(ray_serve_num_router_requests_total[1m]))"
)
_P99_REQUESTS_PER_SECOND_WHILE_SERVING_BY_APPLICATION = (
    f"quantile_over_time(0.99, (({_REQUESTS_PER_SECOND_BY_APPLICATION}) > 0)[30d:1m])"
)
_P99_REFRESH_INTERVAL_S = 600.0


@dataclass
class _PeriodicallyRefreshedQuery:
    promql: str
    refresh_interval_s: float
    value_by_application: dict[str, float] = field(default_factory=dict)
    refreshed_at: float = float("-inf")

    def read(self) -> dict[str, float]:
        now = time.monotonic()
        if now - self.refreshed_at >= self.refresh_interval_s:
            self.value_by_application = _query_by_application(self.promql)
            self.refreshed_at = now
        return self.value_by_application


_p99_requests_per_second_while_serving = _PeriodicallyRefreshedQuery(
    _P99_REQUESTS_PER_SECOND_WHILE_SERVING_BY_APPLICATION, _P99_REFRESH_INTERVAL_S
)


def load_by_application() -> dict[str, float]:
    p99_by_application = _p99_requests_per_second_while_serving.read()
    return {
        application: _load(
            requests_per_second, p99_by_application.get(application, 0.0)
        )
        for application, requests_per_second in _query_by_application(
            _REQUESTS_PER_SECOND_BY_APPLICATION
        ).items()
    }


def _load(requests_per_second: float, p99_requests_per_second: float) -> float:
    if p99_requests_per_second <= 0.0:
        return 0.0
    return requests_per_second / p99_requests_per_second


def _query_by_application(promql: str) -> dict[str, float]:
    return {
        series["metric"]["application"]: float(series["value"][1])
        for series in query_instant(promql)
    }
