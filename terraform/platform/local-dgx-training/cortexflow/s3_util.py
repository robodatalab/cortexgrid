"""S3/MinIO wrappers.

Provides upload/download and a pre-configured boto3 client.
Works with MinIO locally and real S3 on AWS — same code.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from tqdm import tqdm

from cortexflow.config import get_config


def get_s3_client() -> Any:
    """Return a boto3 S3 client configured for MinIO or AWS S3."""
    config = get_config()
    kwargs: dict[str, Any] = {}
    if config.s3_endpoint_url:
        kwargs["endpoint_url"] = config.s3_endpoint_url
    if config.s3_access_key:
        kwargs["aws_access_key_id"] = config.s3_access_key
    if config.s3_secret_key:
        kwargs["aws_secret_access_key"] = config.s3_secret_key
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
    config = get_config()
    bucket = bucket or config.s3_default_bucket
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
