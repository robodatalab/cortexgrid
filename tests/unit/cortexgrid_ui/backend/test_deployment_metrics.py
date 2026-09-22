from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from cortexgrid_ui.backend.main import app
from cortexgrid_ui.backend.models import deployment_metrics
from cortexgrid_ui.backend.models.deployment_metrics import read_deployment_metrics


_APPLICATION = "FLUX.2-klein__4B__imported"


class TestReadDeploymentMetrics(unittest.TestCase):
    def setUp(self) -> None:
        self.promql_asked: list[str] = []
        self.answer_by_metric_name: dict[str, list[dict[str, Any]]] = {}

        def answer(promql: str, start_s: float, end_s: float, step_s: float) -> list[dict[str, Any]]:
            self.promql_asked.append(promql)
            for metric_name, results in self.answer_by_metric_name.items():
                if metric_name in promql:
                    return results
            return []

        patcher = patch.object(deployment_metrics, "query_range", side_effect=answer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_and_response_rates_are_read_for_the_models_application(self) -> None:
        read_deployment_metrics(_APPLICATION, now_s=7200.0)

        rate_queries = [q for q in self.promql_asked if "rate(" in q]
        self.assertEqual(len(rate_queries), 3)
        for promql in rate_queries:
            self.assertIn(f'application="{_APPLICATION}"', promql)

    def test_process_usage_is_read_for_the_models_replicas_only(self) -> None:
        read_deployment_metrics(_APPLICATION, now_s=7200.0)

        usage_queries = [q for q in self.promql_asked if "ray_component_" in q]
        self.assertEqual(len(usage_queries), 4)
        for promql in usage_queries:
            self.assertIn(
                'Component=~"ray::ServeReplica:FLUX\\\\.2\\\\-klein__4B__imported:.*"',
                promql,
            )

    def test_series_are_the_last_hour_of_samples(self) -> None:
        self.answer_by_metric_name["ray_component_cpu_percentage"] = [
            {"metric": {}, "values": [[3600.0, "12.5"], [3630.0, "40"]]}
        ]

        metrics = read_deployment_metrics(_APPLICATION, now_s=7200.0)

        self.assertEqual(metrics.cpu_percent, [(3600.0, 12.5), (3630.0, 40.0)])
        self.assertEqual(metrics.gpu_percent, [])


class TestDeploymentMetricsEndpoint(unittest.TestCase):
    def test_reads_the_metrics_of_the_deployments_serve_app(self) -> None:
        with patch.object(deployment_metrics, "query_range", return_value=[]) as query:
            response = TestClient(app).get(
                "/api/deployments/FLUX.2-klein/4B/imported/metrics"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {
                "requests_per_second",
                "succeeded_responses_per_second",
                "failed_responses_per_second",
                "cpu_percent",
                "memory_bytes",
                "gpu_percent",
                "gpu_memory_bytes",
            },
        )
        self.assertIn(f'application="{_APPLICATION}"', query.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
