"""Secrets API — thin wrapper around AWS Secrets Manager."""

from __future__ import annotations

import time

import boto3  # type: ignore


_SM_PREFIX = "robolab/infra"
_SM_REGION = "us-east-1"
_DELETE_WAIT_TIMEOUT_S = 15.0
_DELETE_WAIT_POLL_S = 0.25


def get_secret(id: str) -> str:
    client = boto3.client("secretsmanager", region_name=_SM_REGION)
    return client.get_secret_value(SecretId=f"{_SM_PREFIX}/{id}")["SecretString"]


def list_secrets() -> list[str]:
    client = boto3.client("secretsmanager", region_name=_SM_REGION)
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
                ids.append(name[len(prefix):])
    return ids


def set_secret(id: str, value: str) -> None:
    client = boto3.client("secretsmanager", region_name=_SM_REGION)
    try:
        client.describe_secret(SecretId=f"{_SM_PREFIX}/{id}")
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=f"{_SM_PREFIX}/{id}", SecretString=value)
        return
    client.put_secret_value(SecretId=f"{_SM_PREFIX}/{id}", SecretString=value)


def delete_secret(id: str) -> None:
    client = boto3.client("secretsmanager", region_name=_SM_REGION)
    secret_id = f"{_SM_PREFIX}/{id}"
    client.delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)
    deadline = time.monotonic() + _DELETE_WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            client.describe_secret(SecretId=secret_id)
        except client.exceptions.ResourceNotFoundException:
            return
        time.sleep(_DELETE_WAIT_POLL_S)
    raise TimeoutError(f"Secret {id!r} still present after {_DELETE_WAIT_TIMEOUT_S}s")
