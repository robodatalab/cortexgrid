"""S3/MinIO wrappers.

Builds an S3 boto3 client from the S3_* entries in AWS Secrets Manager:
S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION, S3_ENDPOINT_URL. They are
fetched at runtime via cortexflow.secrets.get_secret, so no consumer has to
export them as environment variables.

On the AWS profile S3_ENDPOINT_URL is the regional s3.amazonaws.com URL and
S3_* are the real AWS keys; on the on-prem profile S3_ENDPOINT_URL is the
tailnet-reachable MinIO URL and S3_* are the MinIO admin creds.
"""

from __future__ import annotations

import os
from typing import Any

import boto3  # type: ignore
from tqdm import tqdm  # type: ignore

from cortexgrid.infra import get_s3_bucket, get_s3_endpoint_url
from cortexgrid.secrets import get_secret


def get_s3_client() -> Any:
    """Return a boto3 S3 client configured for the cluster's object store."""
    return boto3.client(
        "s3",
        aws_access_key_id=get_secret("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=get_secret("S3_SECRET_ACCESS_KEY"),
        endpoint_url=get_s3_endpoint_url(),
        # Required: without it boto3 picks the local default region for SigV4,
        # which mismatches the AWS endpoint and produces 301 Moved Permanently
        # against real AWS. MinIO ignores the value.
        region_name=get_secret("S3_REGION"),
    )


def _ensure_bucket(client: Any, bucket: str) -> None:
    """Create the bucket if and only if head_bucket returns 404. Other errors
    (region mismatch, perms) propagate so they aren't silently masked by an
    unrelated create_bucket failure."""
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] != "404":
            raise
        # LocationConstraint is required for any AWS region other than us-east-1.
        # MinIO accepts it too.
        client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": get_secret("S3_REGION")},
        )


def upload(local_path: str, dest_path: str | None = None) -> str:
    """Upload a local file to the canonical S3/MinIO bucket.

    Args:
        local_path: Path to the local file.
        dest_path: Full object key in the bucket (folder/filename). Defaults
            to the local file's basename at the bucket root.

    Returns:
        The s3://bucket/<dest_path> URI of the uploaded object.
    """
    bucket = get_s3_bucket()
    dest_path = dest_path or os.path.basename(local_path)

    client = get_s3_client()
    _ensure_bucket(client, bucket)
    file_size = os.path.getsize(local_path)
    with tqdm(
        total=file_size,
        unit="B",
        unit_scale=True,
        desc=f"Uploading {os.path.basename(local_path)}",
    ) as pbar:
        client.upload_file(local_path, bucket, dest_path, Callback=pbar.update)
    return f"s3://{bucket}/{dest_path}"


def upload_dir(local_dir: str, dest_path: str = "") -> list[str]:
    """Upload all files in a directory tree to the canonical S3/MinIO bucket.

    Args:
        local_dir: Path to the local directory.
        dest_path: Folder inside the bucket where the tree lands. Each file's
            key is dest_path/<path relative to local_dir>. Defaults to bucket root.

    Returns:
        List of s3://bucket/<key> URIs for uploaded objects.
    """
    bucket = get_s3_bucket()

    client = get_s3_client()
    _ensure_bucket(client, bucket)

    uploaded: list[str] = []
    for root, _dirs, files in os.walk(local_dir):
        for filename in files:
            local_path = os.path.join(root, filename)
            rel_path = os.path.relpath(local_path, local_dir).replace(os.sep, "/")
            key = f"{dest_path}/{rel_path}" if dest_path else rel_path
            client.upload_file(local_path, bucket, key)
            uploaded.append(f"s3://{bucket}/{key}")

    return uploaded


def delete_prefix(prefix: str) -> None:
    """Delete every object under `prefix` in the canonical bucket."""
    bucket = get_s3_bucket()
    client = get_s3_client()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})


def download(src_path: str, local_path: str | None = None) -> str:
    """Download a file from the canonical S3/MinIO bucket.

    Args:
        src_path: Full object key in the bucket (folder/filename).
        local_path: Where to save locally. Defaults to the src_path basename.

    Returns:
        The local file path.
    """
    local_path = local_path or os.path.basename(src_path)
    client = get_s3_client()
    client.download_file(get_s3_bucket(), src_path, local_path)
    return local_path
