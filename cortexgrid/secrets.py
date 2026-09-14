"""Secrets API - client for the head's secrets server.

The head runs a small HTTP server (k8s/seed/scripts/cortexgrid_head.py) that
keeps every secret in a .env file on the head host. All calls go to
$CORTEXGRID_HEAD_URL: http://robolab-head:7700 from a laptop on the tailnet,
http://cortexgrid-head.default.svc.cluster.local:7700 from pods.

There is no authentication: the head is only reachable over the tailnet.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import requests  # type: ignore


_TIMEOUT_S = 10.0


def _head_url() -> str:
    url = os.environ.get("CORTEXGRID_HEAD_URL")
    if not url:
        raise RuntimeError(
            "CORTEXGRID_HEAD_URL is not set; point it at the head's secrets "
            "server, e.g. http://robolab-head:7700"
        )
    return url.rstrip("/")


def _secret_url(id: str) -> str:
    return f"{_head_url()}/secrets/{quote(id, safe='')}"


def get_secret(id: str) -> str:
    response = requests.get(_secret_url(id), timeout=_TIMEOUT_S)
    response.raise_for_status()
    return response.json()["value"]


def list_secrets() -> list[str]:
    response = requests.get(f"{_head_url()}/secrets", timeout=_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def set_secret(id: str, value: str) -> None:
    response = requests.put(_secret_url(id), json={"value": value}, timeout=_TIMEOUT_S)
    response.raise_for_status()


def delete_secret(id: str) -> None:
    """Idempotent: already-gone is treated as success, matching HTTP DELETE semantics."""
    response = requests.delete(_secret_url(id), timeout=_TIMEOUT_S)
    response.raise_for_status()
