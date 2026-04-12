from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cortexflow_ui.backend.main import app


class TestRunEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("cortexflow_ui.backend.main.list_run_metrics", return_value=["loss", "accuracy"])
    def test_run_metrics_returns_keys(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ["loss", "accuracy"])

    @patch("cortexflow_ui.backend.main.get_metric_history", return_value=[
        {"step": 1, "value": 0.9, "timestamp": 1000},
        {"step": 2, "value": 0.8, "timestamp": 2000},
    ])
    def test_run_metric_history_returns_points(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/metrics/loss")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["step"], 1)
        self.assertEqual(data[1]["value"], 0.8)

    @patch("cortexflow_ui.backend.main.list_run_params", return_value={"lr": "0.001", "epochs": "10"})
    def test_run_params_returns_dict(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/params")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"lr": "0.001", "epochs": "10"})

    @patch("cortexflow_ui.backend.main.list_run_artifacts", return_value=["model.pt", "job/job-1"])
    def test_run_artifacts_returns_paths(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/artifacts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ["model.pt", "job/job-1"])


if __name__ == "__main__":
    unittest.main()
