from __future__ import annotations

import unittest
from unittest.mock import ANY, MagicMock, patch

from cortexflow.config import CortexConfig, set_config


class TestGetS3Client(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(
            s3_endpoint_url="http://localhost:9000",
            s3_access_key="testkey",
            s3_secret_key="testsecret",
        ))

    def tearDown(self) -> None:
        set_config(None)  # type: ignore[arg-type]

    @patch("cortexflow.s3_util.boto3")
    def test_passes_endpoint_and_credentials(self, mock_boto3: MagicMock) -> None:
        from cortexflow.s3_util import get_s3_client
        get_s3_client()
        mock_boto3.client.assert_called_once_with(
            "s3",
            endpoint_url="http://localhost:9000",
            aws_access_key_id="testkey",
            aws_secret_access_key="testsecret",
        )

    @patch("cortexflow.s3_util.boto3")
    def test_omits_empty_fields(self, mock_boto3: MagicMock) -> None:
        set_config(CortexConfig())
        from cortexflow.s3_util import get_s3_client
        get_s3_client()
        mock_boto3.client.assert_called_once_with("s3")


class TestUpload(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(
            s3_endpoint_url="http://localhost:9000",
            s3_access_key="k",
            s3_secret_key="s",
            s3_default_bucket="my-bucket",
        ))

    def tearDown(self) -> None:
        set_config(None)  # type: ignore[arg-type]

    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_uses_default_bucket_and_filename(self, mock_client_fn: MagicMock, _getsize: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        from cortexflow.s3_util import upload
        result = upload("/tmp/data.parquet")

        mock_client.head_bucket.assert_called_once_with(Bucket="my-bucket")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "my-bucket", "data.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://my-bucket/data.parquet")

    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_with_explicit_bucket_and_key(self, mock_client_fn: MagicMock, _getsize: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        from cortexflow.s3_util import upload
        result = upload("/tmp/data.parquet", bucket="other", key="run/output.parquet")

        mock_client.head_bucket.assert_called_once_with(Bucket="other")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "other", "run/output.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://other/run/output.parquet")

    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_creates_bucket_when_missing(self, mock_client_fn: MagicMock, _getsize: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        no_such_bucket = type("NoSuchBucket", (Exception,), {})
        mock_client.exceptions.NoSuchBucket = no_such_bucket
        mock_client.head_bucket.side_effect = no_such_bucket()

        from cortexflow.s3_util import upload
        result = upload("/tmp/data.parquet", bucket="new-bucket", key="file.parquet")

        mock_client.head_bucket.assert_called_once_with(Bucket="new-bucket")
        mock_client.create_bucket.assert_called_once_with(Bucket="new-bucket")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "new-bucket", "file.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://new-bucket/file.parquet")

    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_creates_bucket_on_client_error(self, mock_client_fn: MagicMock, _getsize: MagicMock) -> None:
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        mock_client.exceptions.NoSuchBucket = type("NoSuchBucket", (Exception,), {})
        mock_client.exceptions.ClientError = ClientError
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )

        from cortexflow.s3_util import upload
        result = upload("/tmp/data.parquet", bucket="new-bucket", key="file.parquet")

        mock_client.create_bucket.assert_called_once_with(Bucket="new-bucket")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "new-bucket", "file.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://new-bucket/file.parquet")


class TestDownload(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig())

    def tearDown(self) -> None:
        set_config(None)  # type: ignore[arg-type]

    @patch("cortexflow.s3_util.get_s3_client")
    def test_download_default_local_path(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        from cortexflow.s3_util import download
        result = download("bucket", "path/to/file.csv")

        mock_client.download_file.assert_called_once_with(
            "bucket", "path/to/file.csv", "file.csv"
        )
        self.assertEqual(result, "file.csv")

    @patch("cortexflow.s3_util.get_s3_client")
    def test_download_explicit_local_path(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        from cortexflow.s3_util import download
        result = download("bucket", "key.csv", local_path="/tmp/out.csv")

        mock_client.download_file.assert_called_once_with(
            "bucket", "key.csv", "/tmp/out.csv"
        )
        self.assertEqual(result, "/tmp/out.csv")


if __name__ == "__main__":
    unittest.main()
