"""Secrets API — thin wrapper around AWS Secrets Manager."""

from __future__ import annotations

import boto3  # type: ignore


_SM_PREFIX = "robolab/infra"
_SM_REGION = "us-east-1"


def get_secret(id: str) -> str:
    client = boto3.client("secretsmanager", region_name=_SM_REGION)
    return client.get_secret_value(SecretId=f"{_SM_PREFIX}/{id}")["SecretString"]


def list_secrets() -> list[str]:
    client = boto3.client("secretsmanager", region_name=_SM_REGION)
    names: list[str] = []
    paginator = client.get_paginator("list_secrets")
    for page in paginator.paginate(Filters=[{"Key": "name", "Values": [_SM_PREFIX]}]):
        for entry in page.get("SecretList", []):
            names.append(entry["Name"])
    return names


def set_secret(id: str, value: str) -> None:
    client = boto3.client("secretsmanager", region_name=_SM_REGION)
    try:
        client.describe_secret(SecretId=f"{_SM_PREFIX}/{id}")
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=f"{_SM_PREFIX}/{id}", SecretString=value)
        return
    client.put_secret_value(SecretId=f"{_SM_PREFIX}/{id}", SecretString=value)
