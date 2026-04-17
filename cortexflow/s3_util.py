"""S3/MinIO wrappers.

Provides upload/download and a pre-configured boto3 client.
Works with MinIO locally and real S3 on AWS — same code.
"""

from __future__ import annotations

import os
from typing import Any

import boto3  # type: ignore
from tqdm import tqdm  # type: ignore

from cortexflow.infra import get_s3_endpoint_url


# Hardcoded to match the MinIO creds baked into docker-compose. The DGX
# is an isolated single-tenant machine, so these aren't real secrets.
_S3_ACCESS_KEY = "admin"
_S3_SECRET_KEY = "adminadmin"
_S3_DEFAULT_BUCKET = "ray-checkpoints"


def get_s3_client() -> Any:
    """Return a boto3 S3 client configured for MinIO or AWS S3."""
    kwargs: dict[str, Any] = {}
    endpoint_url = get_s3_endpoint_url()
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    kwargs["aws_access_key_id"] = _S3_ACCESS_KEY
    kwargs["aws_secret_access_key"] = _S3_SECRET_KEY
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
    bucket = bucket or _S3_DEFAULT_BUCKET
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
    bucket = bucket or _S3_DEFAULT_BUCKET

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
