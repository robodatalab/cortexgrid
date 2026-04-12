from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import ANY, MagicMock, patch

from botocore.exceptions import ClientError  # type: ignore
from cortexflow.s3_util import download, upload, upload_dir


class TestS3Client(unittest.TestCase):

    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_uses_default_bucket_and_filename(
        self, mock_client_fn: MagicMock, _getsize: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = upload("/tmp/data.parquet")

        mock_client.head_bucket.assert_called_once_with(Bucket="ray-checkpoints")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "ray-checkpoints", "data.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://ray-checkpoints/data.parquet")

    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_with_explicit_bucket_and_key(
        self, mock_client_fn: MagicMock, _getsize: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = upload("/tmp/data.parquet", bucket="other", key="run/output.parquet")

        mock_client.head_bucket.assert_called_once_with(Bucket="other")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "other", "run/output.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://other/run/output.parquet")

    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_creates_bucket_when_missing(
        self, mock_client_fn: MagicMock, _getsize: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        no_such_bucket = type("NoSuchBucket", (Exception,), {})
        mock_client.exceptions.NoSuchBucket = no_such_bucket
        mock_client.head_bucket.side_effect = no_such_bucket()

        result = upload("/tmp/data.parquet", bucket="new-bucket", key="file.parquet")

        mock_client.head_bucket.assert_called_once_with(Bucket="new-bucket")
        mock_client.create_bucket.assert_called_once_with(Bucket="new-bucket")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "new-bucket", "file.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://new-bucket/file.parquet")

    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_creates_bucket_on_client_error(
        self, mock_client_fn: MagicMock, _getsize: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        mock_client.exceptions.NoSuchBucket = type("NoSuchBucket", (Exception,), {})
        mock_client.exceptions.ClientError = ClientError
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )

        result = upload("/tmp/data.parquet", bucket="new-bucket", key="file.parquet")

        mock_client.create_bucket.assert_called_once_with(Bucket="new-bucket")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "new-bucket", "file.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://new-bucket/file.parquet")


    @patch("cortexflow.s3_util.get_s3_client")
    def test_uploads_all_files(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "sub"))
            with open(os.path.join(tmpdir, "a.txt"), "w") as f:
                f.write("hello")
            with open(os.path.join(tmpdir, "sub", "b.txt"), "w") as f:
                f.write("world")

            result = upload_dir(tmpdir, bucket="out", prefix="model")

        self.assertEqual(len(result), 2)
        self.assertIn("s3://out/model/a.txt", result)
        self.assertIn("s3://out/model/sub/b.txt", result)

    @patch("cortexflow.s3_util.get_s3_client")
    def test_uses_default_bucket(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "f.txt"), "w") as f:
                f.write("data")

            result = upload_dir(tmpdir)

        self.assertEqual(result, ["s3://ray-checkpoints/f.txt"])

    @patch("cortexflow.s3_util.get_s3_client")
    def test_download_default_local_path(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = download("bucket", "path/to/file.csv")

        mock_client.download_file.assert_called_once_with(
            "bucket", "path/to/file.csv", "file.csv"
        )
        self.assertEqual(result, "file.csv")

    @patch("cortexflow.s3_util.get_s3_client")
    def test_download_explicit_local_path(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = download("bucket", "key.csv", local_path="/tmp/out.csv")

        mock_client.download_file.assert_called_once_with(
            "bucket", "key.csv", "/tmp/out.csv"
        )
        self.assertEqual(result, "/tmp/out.csv")


if __name__ == "__main__":
    unittest.main()
