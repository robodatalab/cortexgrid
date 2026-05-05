from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cortexflow.infra import (
    get_mlflow_run_url,
    get_mlflow_tracking_uri,
    get_ray_job_server_uri,
    get_s3_endpoint_url,
)


class TestInfraReadsFromSm(unittest.TestCase):
    """All cortexflow config -- mlflow URI, ray URI, S3 endpoint -- is read
    from AWS Secrets Manager. No env-var override path exists."""

    @patch("cortexflow.infra.get_secret", return_value="http://100.111.172.6:30500")
    def test_mlflow_tracking_uri(self, mock_secret: MagicMock) -> None:
        self.assertEqual(get_mlflow_tracking_uri(), "http://100.111.172.6:30500")
        mock_secret.assert_called_once_with("MLFLOW_TRACKING_URI")

    @patch("cortexflow.infra.get_secret", return_value="http://100.111.172.6:30265")
    def test_ray_job_server_uri(self, mock_secret: MagicMock) -> None:
        self.assertEqual(get_ray_job_server_uri(), "http://100.111.172.6:30265")
        mock_secret.assert_called_once_with("RAY_JOB_SERVER_URI")

    @patch("cortexflow.infra.get_secret", return_value="")
    def test_s3_endpoint_url_empty_means_real_aws_s3(self, mock_secret: MagicMock) -> None:
        # AWS profile writes "" to SM; consumers treat empty as "no override".
        self.assertEqual(get_s3_endpoint_url(), "")
        mock_secret.assert_called_once_with("AWS_S3_ENDPOINT_URL")

    @patch(
        "cortexflow.infra.get_secret",
        return_value="http://minio.minio.svc.cluster.local:9000",
    )
    def test_s3_endpoint_url_onprem_minio(self, mock_secret: MagicMock) -> None:
        self.assertEqual(
            get_s3_endpoint_url(), "http://minio.minio.svc.cluster.local:9000"
        )

    @patch("cortexflow.infra.MlflowClient")
    @patch("cortexflow.infra.get_secret", return_value="http://100.111.172.6:30500")
    def test_mlflow_run_url(
        self, _mock_secret: MagicMock, mock_mlflow_cls: MagicMock
    ) -> None:
        run = MagicMock()
        run.info.experiment_id = "7"
        fake_client = MagicMock()
        fake_client.get_run.return_value = run
        mock_mlflow_cls.return_value = fake_client

        url = get_mlflow_run_url("run-xyz")

        self.assertEqual(
            url, "http://100.111.172.6:30500/#/experiments/7/runs/run-xyz"
        )


if __name__ == "__main__":
    unittest.main()
