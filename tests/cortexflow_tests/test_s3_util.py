from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import ANY, MagicMock, patch

from botocore.exceptions import ClientError  # type: ignore
from cortexflow.s3_util import download, upload, upload_dir


class TestS3Client(unittest.TestCase):

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_default_key_at_bucket_root(
        self, mock_client_fn: MagicMock, _getsize: MagicMock, _bucket: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = upload("/tmp/data.parquet")

        mock_client.head_bucket.assert_called_once_with(Bucket="canonical")
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "canonical", "data.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://canonical/data.parquet")

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_with_explicit_key(
        self, mock_client_fn: MagicMock, _getsize: MagicMock, _bucket: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = upload("/tmp/data.parquet", key="run/output.parquet")

        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "canonical", "run/output.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://canonical/run/output.parquet")

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_with_dest_root_folder(
        self, mock_client_fn: MagicMock, _getsize: MagicMock, _bucket: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = upload(
            "/tmp/data.parquet", dest_root_folder="app", key="run/output.parquet"
        )

        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "canonical", "app/run/output.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://canonical/app/run/output.parquet")

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.get_aws_region", return_value="eu-west-2")
    @patch("cortexflow.s3_util.os.path.getsize", return_value=1024)
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_creates_bucket_when_missing(
        self,
        mock_client_fn: MagicMock,
        _getsize: MagicMock,
        _region: MagicMock,
        _bucket: MagicMock,
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        mock_client.exceptions.ClientError = ClientError
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )

        result = upload("/tmp/data.parquet", key="file.parquet")

        mock_client.head_bucket.assert_called_once_with(Bucket="canonical")
        mock_client.create_bucket.assert_called_once_with(
            Bucket="canonical",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        mock_client.upload_file.assert_called_once_with(
            "/tmp/data.parquet", "canonical", "file.parquet", Callback=ANY
        )
        self.assertEqual(result, "s3://canonical/file.parquet")

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_dir_at_bucket_root(
        self, mock_client_fn: MagicMock, _bucket: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "f.txt"), "w") as f:
                f.write("data")

            result = upload_dir(tmpdir)

        self.assertEqual(result, ["s3://canonical/f.txt"])

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.get_s3_client")
    def test_upload_dir_with_dest_root_folder_and_prefix(
        self, mock_client_fn: MagicMock, _bucket: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "sub"))
            with open(os.path.join(tmpdir, "a.txt"), "w") as f:
                f.write("hello")
            with open(os.path.join(tmpdir, "sub", "b.txt"), "w") as f:
                f.write("world")

            result = upload_dir(tmpdir, dest_root_folder="app", prefix="model")

        self.assertEqual(len(result), 2)
        self.assertIn("s3://canonical/app/model/a.txt", result)
        self.assertIn("s3://canonical/app/model/sub/b.txt", result)

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.get_s3_client")
    def test_download_default_local_path(
        self, mock_client_fn: MagicMock, _bucket: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = download("path/to/file.csv")

        mock_client.download_file.assert_called_once_with(
            "canonical", "path/to/file.csv", "file.csv"
        )
        self.assertEqual(result, "file.csv")

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.get_s3_client")
    def test_download_with_src_root_folder(
        self, mock_client_fn: MagicMock, _bucket: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = download("file.csv", src_root_folder="app/run")

        mock_client.download_file.assert_called_once_with(
            "canonical", "app/run/file.csv", "file.csv"
        )
        self.assertEqual(result, "file.csv")

    @patch("cortexflow.s3_util.get_s3_bucket", return_value="canonical")
    @patch("cortexflow.s3_util.get_s3_client")
    def test_download_explicit_local_path(
        self, mock_client_fn: MagicMock, _bucket: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        result = download("key.csv", local_path="/tmp/out.csv")

        mock_client.download_file.assert_called_once_with(
            "canonical", "key.csv", "/tmp/out.csv"
        )
        self.assertEqual(result, "/tmp/out.csv")


if __name__ == "__main__":
    unittest.main()
