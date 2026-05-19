from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from cortexflow.infra import (
    get_mlflow_run_url,
    get_mlflow_tracking_uri,
    get_ray_job_server_uri,
    get_s3_endpoint_url,
)


class TestInfraReadsFromSm(unittest.TestCase):
    """mlflow + ray URIs come from AWS Secrets Manager; S3 endpoint and bucket
    name come from S3_* env vars surfaced by the s3-creds Secret."""

    @patch("cortexflow.infra.get_secret", return_value="http://100.111.172.6:30500")
    def test_mlflow_tracking_uri(self, mock_secret: MagicMock) -> None:
        self.assertEqual(get_mlflow_tracking_uri(), "http://100.111.172.6:30500")
        mock_secret.assert_called_once_with("MLFLOW_TRACKING_URI")

    @patch("cortexflow.infra.get_secret", return_value="http://100.111.172.6:30265")
    def test_ray_job_server_uri(self, mock_secret: MagicMock) -> None:
        self.assertEqual(get_ray_job_server_uri(), "http://100.111.172.6:30265")
        mock_secret.assert_called_once_with("RAY_JOB_SERVER_URI")

    @patch.dict(os.environ, {"S3_ENDPOINT_URL": ""})
    def test_s3_endpoint_url_empty_means_real_aws_s3(self) -> None:
        # AWS profile sets S3_ENDPOINT_URL to ""; consumers treat empty as
        # "no override" so boto3 hits the regional s3.amazonaws.com URL.
        self.assertEqual(get_s3_endpoint_url(), "")

    @patch.dict(
        os.environ,
        {"S3_ENDPOINT_URL": "http://minio.minio.svc.cluster.local:9000"},
    )
    def test_s3_endpoint_url_onprem_minio(self) -> None:
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
