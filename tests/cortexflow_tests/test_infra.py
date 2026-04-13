from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cortexflow.infra import (
    get_mlflow_run_url,
    get_mlflow_tracking_uri,
    get_ray_job_server_uri,
    get_s3_endpoint_url,
    set_runs_on_server,
)


class TestInfraOffServer(unittest.TestCase):
    def setUp(self) -> None:
        set_runs_on_server(False)
        self.addCleanup(set_runs_on_server, False)

    @patch("cortexflow.infra.get_secret", return_value="100.80.27.32")
    def test_get_mlflow_tracking_uri_uses_server_ip(self, _mock: MagicMock) -> None:
        self.assertEqual(get_mlflow_tracking_uri(), "http://100.80.27.32:5000")

    @patch("cortexflow.infra.get_secret", return_value="100.80.27.32")
    def test_get_ray_job_server_uri_uses_server_ip(self, _mock: MagicMock) -> None:
        self.assertEqual(get_ray_job_server_uri(), "http://100.80.27.32:8265")

    @patch("cortexflow.infra.get_secret", return_value="100.80.27.32")
    def test_get_s3_endpoint_url_uses_server_ip(self, _mock: MagicMock) -> None:
        self.assertEqual(get_s3_endpoint_url(), "http://100.80.27.32:9000")

    @patch("cortexflow.infra.MlflowClient")
    @patch("cortexflow.infra.get_secret", return_value="100.80.27.32")
    def test_get_mlflow_run_url_builds_url(
        self, _mock_secret: MagicMock, mock_mlflow_cls: MagicMock
    ) -> None:
        fake_client = MagicMock()
        run = MagicMock()
        run.info.experiment_id = "7"
        fake_client.get_run.return_value = run
        mock_mlflow_cls.return_value = fake_client

        url = get_mlflow_run_url("run-xyz")

        self.assertEqual(url, "http://100.80.27.32:5000/#/experiments/7/runs/run-xyz")


class TestInfraOnServer(unittest.TestCase):
    def setUp(self) -> None:
        set_runs_on_server(True)
        self.addCleanup(set_runs_on_server, False)

    def test_get_mlflow_tracking_uri_uses_internal_dns(self) -> None:
        self.assertEqual(get_mlflow_tracking_uri(), "http://mlflow:5000")

    def test_get_ray_job_server_uri_uses_internal_dns(self) -> None:
        self.assertEqual(get_ray_job_server_uri(), "http://ray-head:8265")

    def test_get_s3_endpoint_url_uses_internal_dns(self) -> None:
        self.assertEqual(get_s3_endpoint_url(), "http://minio:9000")


if __name__ == "__main__":
    unittest.main()
