"""Secrets API - thin wrapper around AWS Secrets Manager.

The boto3 client is built explicitly from SM_ACCESS_KEY_ID, SM_SECRET_ACCESS_KEY
and SM_REGION env vars when present. Pods get those from the sm-creds Secret;
laptops that haven't exported them fall back to boto3's default credential
chain (~/.aws/credentials etc.). Keeping the SM creds in their own env-var
namespace means S3_* and ROUTE53_* identities can coexist in the same pod
without colliding through the default AWS_* chain.
"""

from __future__ import annotations

import os
import time
from typing import Any

import boto3  # type: ignore


_SM_PREFIX = "robolab/infra"
_CONSISTENCY_TIMEOUT_S = 15.0
_CONSISTENCY_POLL_S = 0.25


def _sm_client() -> Any:
    access_key = os.environ.get("SM_ACCESS_KEY_ID")
    secret_key = os.environ.get("SM_SECRET_ACCESS_KEY")
    region = os.environ.get("SM_REGION")
    if access_key and secret_key:
        return boto3.client(
            "secretsmanager",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
    return boto3.client("secretsmanager", region_name=region)


def get_secret(id: str) -> str:
    client = _sm_client()
    return client.get_secret_value(SecretId=f"{_SM_PREFIX}/{id}")["SecretString"]


def list_secrets() -> list[str]:
    client = _sm_client()
    ids: list[str] = []
    paginator = client.get_paginator("list_secrets")
    prefix = f"{_SM_PREFIX}/"
    pages = paginator.paginate(
        Filters=[{"Key": "name", "Values": [_SM_PREFIX]}],
        IncludePlannedDeletion=False,
    )
    for page in pages:
        for entry in page.get("SecretList", []):
            if entry.get("DeletedDate") is not None:
                continue
            name = entry["Name"]
            if name.startswith(prefix):
                ids.append(name[len(prefix) :])
    return ids


def set_secret(id: str, value: str) -> None:
    client = _sm_client()
    secret_id = f"{_SM_PREFIX}/{id}"
    try:
        client.describe_secret(SecretId=secret_id)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=secret_id, SecretString=value)
    else:
        client.put_secret_value(SecretId=secret_id, SecretString=value)
    deadline = time.monotonic() + _CONSISTENCY_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            if client.get_secret_value(SecretId=secret_id)["SecretString"] == value:
                return
        except client.exceptions.ResourceNotFoundException:
            pass
        time.sleep(_CONSISTENCY_POLL_S)
    raise TimeoutError(
        f"Secret {id!r} not readable as set value after {_CONSISTENCY_TIMEOUT_S}s"
    )


def delete_secret(id: str) -> None:
    """Idempotent: already-gone is treated as success, matching HTTP DELETE semantics."""
    client = _sm_client()
    secret_id = f"{_SM_PREFIX}/{id}"
    try:
        client.delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)
    except client.exceptions.ResourceNotFoundException:
        return
    deadline = time.monotonic() + _CONSISTENCY_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            client.describe_secret(SecretId=secret_id)
        except client.exceptions.ResourceNotFoundException:
            return
        time.sleep(_CONSISTENCY_POLL_S)
    raise TimeoutError(f"Secret {id!r} still present after {_CONSISTENCY_TIMEOUT_S}s")
