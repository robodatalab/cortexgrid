"""S3/MinIO wrappers.

Provides upload/download and a pre-configured boto3 client. AWS or MinIO is
selected by what is in robolab/infra/AWS_S3_ENDPOINT_URL: empty means real S3,
non-empty means MinIO at that URL.

Credentials come from the standard boto3 chain (AWS env vars in pods,
~/.aws/credentials on the laptop). All cortexflow config -- bucket name and
S3 endpoint -- lives in AWS Secrets Manager, never in env vars.
"""

from __future__ import annotations

import os
from typing import Any

import boto3  # type: ignore
from tqdm import tqdm  # type: ignore

from cortexflow.infra import get_s3_endpoint_url
from cortexflow.secrets import get_secret


def _default_bucket() -> str:
    return get_secret("S3_BUCKET_NAME")


def get_s3_client() -> Any:
    """Return a boto3 S3 client built from SM-stored S3 creds + endpoint.

    S3 access creds (S3_ACCESS_KEY_ID/SECRET in SM) are deliberately separate
    from the AWS keys that boto3's default chain picks up from env. The env
    creds (AWS_ACCESS_KEY_ID/SECRET in pod env from the aws-creds Secret) are
    real AWS keys used only to reach AWS Secrets Manager. The S3 creds are
    profile-specific: real-AWS on the AWS profile (mirrors AWS_*), MinIO admin
    on the on-prem profile. Building the client explicitly avoids leaking the
    AWS-SM keys into S3 calls (which on-prem MinIO would reject).
    """
    return boto3.client(
        "s3",
        aws_access_key_id=get_secret("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=get_secret("S3_SECRET_ACCESS_KEY"),
        endpoint_url=get_s3_endpoint_url(),
    )


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
