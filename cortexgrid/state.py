"""Client for cortexgrid's own records, which the jobs control plane keeps in
Postgres: experiments and runs (mapped onto their MLflow counterparts), jobs
with their manifests, results and checkpoints, the model registry, and the
models deployed on Ray Serve.

One HTTP call per read or write over one keep-alive session, so a lookup
costs one round trip to the control plane. Path segments are quoted here, so
callers pass them as they are. The routes are served by
jobs_control_plane/api.py."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests  # type: ignore

from cortexgrid.infra import get_jobs_control_plane_uri


_TIMEOUT_S = 30.0
_session = requests.Session()


def _url(segments: tuple[str, ...]) -> str:
    return "/".join(
        [get_jobs_control_plane_uri().rstrip("/"), *(quote(s, safe="") for s in segments)]
    )


def get(*segments: str, params: dict[str, str] | None = None) -> Any:
    """The JSON record at the path, or None when there is none."""
    response = _session.get(_url(segments), params=params, timeout=_TIMEOUT_S)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def get_bytes(*segments: str) -> bytes | None:
    """The binary record at the path, or None when there is none."""
    response = _session.get(_url(segments), timeout=_TIMEOUT_S)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def put(*segments: str, body: Any, params: dict[str, str] | None = None) -> Any:
    """Write the record at the path; returns what the control plane answers."""
    response = _session.put(
        _url(segments), json=body, params=params, timeout=_TIMEOUT_S
    )
    response.raise_for_status()
    return response.json()


def put_bytes(*segments: str, body: bytes) -> None:
    _session.put(
        _url(segments),
        data=body,
        headers={"Content-Type": "application/octet-stream"},
        timeout=_TIMEOUT_S,
    ).raise_for_status()


def patch(*segments: str, body: Any, params: dict[str, str] | None = None) -> bool:
    """Merge `body` into the record at the path. False when there is none."""
    response = _session.patch(
        _url(segments), json=body, params=params, timeout=_TIMEOUT_S
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def post(*segments: str) -> None:
    _session.post(_url(segments), timeout=_TIMEOUT_S).raise_for_status()


def delete(*segments: str, params: dict[str, str] | None = None) -> None:
    """Idempotent: a record already gone is not an error."""
    _session.delete(_url(segments), params=params, timeout=_TIMEOUT_S).raise_for_status()
