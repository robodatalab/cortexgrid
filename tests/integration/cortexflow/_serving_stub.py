"""Test fixture for the model_serving integration test.

Defines a Ray Serve deployment that reads marker.json from the cortexflow
registry on startup, plus helpers for contacting the deployment from local
test code or from inside a Ray job.

Bundled into the cortexflow.remote() working_dir so contact_deployment is
importable on a Ray worker.
"""

from __future__ import annotations

import json
import time

import requests
from fastapi import FastAPI
from ray import serve

import cortexflow
from cortexflow.ray_util import get_serve_details


_app = FastAPI()


@cortexflow.model_deployment(num_gpus=0, num_replicas=1)
@serve.ingress(_app)
class CheckpointReadingStub:
    """Loads marker.json from the named model on startup; replays it via /marker."""

    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        path = cortexflow.load_model(family, suffix, run_name)
        self._marker = json.loads((path / "marker.json").read_text())

    @_app.get("/marker")
    def marker(self) -> dict:
        return self._marker


def wait_for_endpoint(url: str, timeout_s: float = 30) -> requests.Response:
    """serve.run returns when the controller accepts the app, but the replica
    may still be booting. Retry the GET until it succeeds or we time out."""
    deadline = time.monotonic() + timeout_s
    last_error = "(probe never ran)"
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                return response
            last_error = f"{response.status_code}: {response.text}"
        except requests.RequestException as exc:
            last_error = repr(exc)
        time.sleep(1)
    raise AssertionError(
        f"GET {url} never succeeded: {last_error}\n"
        f"Serve controller view: {_format_serve_apps()}"
    )


def _format_serve_apps() -> str:
    """Project the Serve controller's app+deployment statuses into a compact
    string for failure diagnostics. Swallows any dashboard error so a probe
    timeout still raises the original assertion."""
    try:
        details = get_serve_details()
    except Exception as exc:
        return f"<get_serve_details failed: {exc!r}>"
    apps = []
    for name, app in details.get("applications", {}).items():
        deployments = {
            dname: {
                "status": d.get("status"),
                "message": d.get("message"),
                "replica_states": d.get("replica_states"),
            }
            for dname, d in app.get("deployments", {}).items()
        }
        apps.append(
            {
                "name": name,
                "status": app.get("status"),
                "message": app.get("message"),
                "route_prefix": app.get("route_prefix"),
                "deployments": deployments,
            }
        )
    return json.dumps(apps, default=str)


def contact_deployment(url: str, expected_marker: str) -> None:
    """Hit the deployment's /marker endpoint and assert the body matches.

    Defined at module level so it can be picked up by cortexflow.remote()
    and run on a Ray worker.
    """
    response = wait_for_endpoint(f"{url}/marker")
    assert response.json() == {"marker": expected_marker}, response.json()
