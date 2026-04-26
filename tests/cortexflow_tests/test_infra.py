from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cortexflow.infra import (
    get_mlflow_run_url,
    get_mlflow_tracking_uri,
    get_ray_job_server_uri,
    get_s3_endpoint_url,
)


class TestInfraFallsBackToSm(unittest.TestCase):
    """With no env vars set, URIs come from AWS Secrets Manager — which now holds
    the full URL (not just an IP). `get_s3_endpoint_url` is env-only with no SM
    fallback: unset means "real AWS S3"."""

    @patch("cortexflow.infra.get_secret", return_value="http://100.80.27.32:30500")
    def test_mlflow_tracking_uri(self, _mock: MagicMock) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(get_mlflow_tracking_uri(), "http://100.80.27.32:30500")

    @patch("cortexflow.infra.get_secret", return_value="http://100.80.27.32:30265")
    def test_ray_job_server_uri(self, _mock: MagicMock) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(get_ray_job_server_uri(), "http://100.80.27.32:30265")

    def test_s3_endpoint_url_unset_returns_none(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(get_s3_endpoint_url())

    @patch("cortexflow.infra.MlflowClient")
    @patch("cortexflow.infra.get_secret", return_value="http://100.80.27.32:30500")
    def test_mlflow_run_url(
        self, _mock_secret: MagicMock, mock_mlflow_cls: MagicMock
    ) -> None:
        run = MagicMock()
        run.info.experiment_id = "7"
        fake_client = MagicMock()
        fake_client.get_run.return_value = run
        mock_mlflow_cls.return_value = fake_client

        with patch.dict("os.environ", {}, clear=True):
            url = get_mlflow_run_url("run-xyz")

        self.assertEqual(
            url, "http://100.80.27.32:30500/#/experiments/7/runs/run-xyz"
        )


class TestInfraEnvVarsWin(unittest.TestCase):
    """When env vars are set, they take precedence over the fallback."""

    def test_mlflow_tracking_uri_from_env(self) -> None:
        with patch.dict(
            "os.environ",
            {"MLFLOW_TRACKING_URI": "http://mlflow.mlflow.svc.cluster.local:5000"},
        ):
            self.assertEqual(
                get_mlflow_tracking_uri(),
                "http://mlflow.mlflow.svc.cluster.local:5000",
            )

    def test_ray_job_server_uri_from_env(self) -> None:
        with patch.dict(
            "os.environ",
            {"RAY_JOB_SERVER_URI": "http://ray-head.ray.svc.cluster.local:8265"},
        ):
            self.assertEqual(
                get_ray_job_server_uri(),
                "http://ray-head.ray.svc.cluster.local:8265",
            )

    def test_s3_endpoint_url_from_env(self) -> None:
        with patch.dict(
            "os.environ",
            {"AWS_S3_ENDPOINT_URL": "http://minio.minio.svc.cluster.local:9000"},
        ):
            self.assertEqual(
                get_s3_endpoint_url(),
                "http://minio.minio.svc.cluster.local:9000",
            )


if __name__ == "__main__":
    unittest.main()
