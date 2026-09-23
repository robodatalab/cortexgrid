from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from cortexgrid.model_serving import Deployment, DeploymentKey
from cortexgrid_ui.backend.main import app
from cortexgrid_ui.backend.models import deployment_load
from cortexgrid_ui.backend.models.deployment_load import (
    _P99_REFRESH_INTERVAL_S,
    _P99_REQUESTS_PER_SECOND_WHILE_SERVING_BY_APPLICATION,
    _REQUESTS_PER_SECOND_BY_APPLICATION,
    _PeriodicallyRefreshedQuery,
    load_by_application,
)
from cortexgrid_ui.backend.streams import deployments_stream


class TestLoadByApplication(unittest.TestCase):
    def setUp(self) -> None:
        self.requests_per_second: dict[str, float] = {}
        self.p99_requests_per_second_while_serving: dict[str, float] = {}
        self.queries: list[str] = []

        def answer(promql: str) -> dict[str, float]:
            self.queries.append(promql)
            if promql == _REQUESTS_PER_SECOND_BY_APPLICATION:
                return self.requests_per_second
            return self.p99_requests_per_second_while_serving

        patchers = [
            patch.object(deployment_load, "_query_by_application", side_effect=answer),
            patch.object(
                deployment_load,
                "_p99_requests_per_second_while_serving",
                _PeriodicallyRefreshedQuery(
                    _P99_REQUESTS_PER_SECOND_WHILE_SERVING_BY_APPLICATION,
                    _P99_REFRESH_INTERVAL_S,
                ),
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_load_is_the_request_rate_over_its_p99_while_serving(self) -> None:
        self.requests_per_second = {"flux": 0.25}
        self.p99_requests_per_second_while_serving = {"flux": 0.5}

        self.assertEqual(load_by_application(), {"flux": 0.5})

    def test_a_model_that_never_served_has_no_load(self) -> None:
        self.requests_per_second = {"fresh": 0.0}

        self.assertEqual(load_by_application(), {"fresh": 0.0})

    def test_the_p99_is_read_once_per_refresh_interval(self) -> None:
        load_by_application()
        load_by_application()

        self.assertEqual(
            self.queries.count(_P99_REQUESTS_PER_SECOND_WHILE_SERVING_BY_APPLICATION), 1
        )
        self.assertEqual(self.queries.count(_REQUESTS_PER_SECOND_BY_APPLICATION), 2)


class TestDeploymentsLoadEndpoint(unittest.TestCase):
    def test_reports_each_deployments_load_by_deployment_id(self) -> None:
        deployments_stream.deployments_cache.set(
            deployments_stream.META_TOPIC,
            {
                "flux/klein/imported": Deployment(
                    DeploymentKey("flux", "klein", "imported"),
                    {},
                    "http://serve/r/flux/klein/imported",
                    "running",
                    "",
                    "",
                    "",
                ),
                "qwen/3b/imported": Deployment(
                    DeploymentKey("qwen", "3b", "imported"),
                    {},
                    "http://serve/r/qwen/3b/imported",
                    "paused",
                    "",
                    "",
                    "",
                ),
            },
        )
        self.addCleanup(deployments_stream.deployments_cache.clear, deployments_stream.META_TOPIC)

        with patch(
            "cortexgrid_ui.backend.main.load_by_application",
            return_value={"flux__klein__imported": 0.9},
        ):
            response = TestClient(app).get("/api/deployments/load")

        self.assertEqual(
            response.json(), {"flux/klein/imported": 0.9, "qwen/3b/imported": 0.0}
        )


if __name__ == "__main__":
    unittest.main()
