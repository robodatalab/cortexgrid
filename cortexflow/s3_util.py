"""S3/MinIO wrappers.

Provides upload/download and a pre-configured boto3 client.
Works with real S3 on AWS and with MinIO when AWS_S3_ENDPOINT_URL is set.

Credentials come from the standard boto3 chain (AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY env vars; on AWS this is populated by ESO from
robolab/infra/AWS_*).

Default bucket comes from S3_BUCKET_NAME env (populated by ESO from
robolab/infra/S3_BUCKET_NAME, written by terraform/platform/s3).
"""

from __future__ import annotations

import os
from typing import Any

import boto3  # type: ignore
from tqdm import tqdm  # type: ignore

from cortexflow.infra import get_s3_endpoint_url


def _default_bucket() -> str:
    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        raise RuntimeError(
            "S3_BUCKET_NAME env var is not set. Apps running in-cluster get "
            "this via ESO from the aws-creds Secret; set it manually in dev "
            "shells if you need to call upload/download outside the cluster."
        )
    return bucket


def get_s3_client() -> Any:
    """Return a boto3 S3 client. AWS by default, MinIO if AWS_S3_ENDPOINT_URL is set."""
    kwargs: dict[str, Any] = {}
    if endpoint_url := get_s3_endpoint_url():
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client("s3", **kwargs)


def upload(
    local_path: str,
    bucket: str | None = None,
    key: str | None = None,
) -> str:
    """Upload a local file to S3/MinIO.

    Args:
        local_path: Path to the local file.
        bucket: Target bucket. Defaults to the configured default bucket.
        key: Object key. Defaults to the filename.

    Returns:
        The s3://bucket/key URI of the uploaded object.
    """
    bucket = bucket or _default_bucket()
    key = key or os.path.basename(local_path)

    client = get_s3_client()
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.NoSuchBucket:
        client.create_bucket(Bucket=bucket)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=bucket)
    file_size = os.path.getsize(local_path)
    with tqdm(
        total=file_size,
        unit="B",
        unit_scale=True,
        desc=f"Uploading {os.path.basename(local_path)}",
    ) as pbar:
        client.upload_file(local_path, bucket, key, Callback=pbar.update)
    return f"s3://{bucket}/{key}"


def upload_dir(
    local_dir: str,
    bucket: str | None = None,
    prefix: str = "",
) -> list[str]:
    """Upload all files in a directory tree to S3/MinIO.

    Args:
        local_dir: Path to the local directory.
        bucket: Target bucket. Defaults to the configured default bucket.
        prefix: Key prefix for all uploaded objects.

    Returns:
        List of s3://bucket/key URIs for uploaded objects.
    """
    bucket = bucket or _default_bucket()

    client = get_s3_client()
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.NoSuchBucket:
        client.create_bucket(Bucket=bucket)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=bucket)

    uploaded: list[str] = []
    for root, _dirs, files in os.walk(local_dir):
        for filename in files:
            local_path = os.path.join(root, filename)
            rel_path = os.path.relpath(local_path, local_dir).replace(os.sep, "/")
            key = f"{prefix}/{rel_path}" if prefix else rel_path
            client.upload_file(local_path, bucket, key)
            uploaded.append(f"s3://{bucket}/{key}")

    return uploaded


def download(
    bucket: str,
    key: str,
    local_path: str | None = None,
) -> str:
    """Download a file from S3/MinIO.

    Args:
        bucket: Source bucket.
        key: Object key.
        local_path: Where to save locally. Defaults to the key's basename.

    Returns:
        The local file path.
    """
    local_path = local_path or os.path.basename(key)
    client = get_s3_client()
    client.download_file(bucket, key, local_path)
    return local_path
