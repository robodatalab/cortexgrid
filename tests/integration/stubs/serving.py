"""Test fixtures for the model_serving integration tests.

Under the current cortexgrid contract the stored model and the serve-app are
separate objects:

  - the stored "weights" are an opaque directory - here a single weights.json
    holding one integer constant, the kind of artifact a training job produces
    before `cortexgrid.save_model(weights_dir, ...)`;
  - the serve-app is a `cortexgrid.serve.ingress` class that downloads that
    directory at startup via `cortexgrid.load_model` and exposes its own route.

`contact_deployment` exercises the deploy-then-call path end-to-end and lives
at module level so `cortexgrid.remote()` can ship it to a Ray worker.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests
from fastapi import FastAPI

import cortexgrid
from cortexgrid import serve


_CONSTANT_FILE = "weights.json"
OFFSET_PARAM = "offset"


def write_weights(d: Path, constant: int) -> None:
    """Write the stub's 'trained weights' (a single integer) into directory `d`,
    the way a training job would before `cortexgrid.save_model`."""
    (d / _CONSTANT_FILE).write_text(json.dumps({"constant": constant}))


def read_constant(weights_dir: Path) -> int:
    """Read the constant back out of a weights directory."""
    return json.loads((weights_dir / _CONSTANT_FILE).read_text())["constant"]


_app = FastAPI()


@serve.ingress(_app)
class AddConstantServeApp:
    """Serve-app fronting the stub weights. Loads the constant from the weights
    directory at startup and returns `x + constant` on its own POST /add route.
    Saved without requirements, so it runs on any node, CPU-only included."""

    def __init__(self, deployment: cortexgrid.DeploymentKey) -> None:
        weights_dir = cortexgrid.load_model(
            deployment.family, deployment.suffix, deployment.run_name
        )
        config = cortexgrid.model_config(deployment)
        self._constant = read_constant(weights_dir) + int(config.get(OFFSET_PARAM, "0"))

    @_app.post("/add")
    async def add(self, body: dict) -> dict:
        return {"result": body["x"] + self._constant}


def contact_deployment(
    family: str, suffix: str, run_name: str, x: int, expected: int
) -> None:
    """Re-resolve a deployment by triple, POST to its route, assert the answer.

    Lives at module level so `cortexgrid.remote()` can ship it to a Ray worker.
    Builds its own HTTP client against the Deployment URL - cortexgrid no longer
    provides an inference proxy."""
    deployed = cortexgrid.deploy_model(family, suffix, run_name)
    response = requests.post(f"{deployed.url}/add", json={"x": x}, timeout=60)
    response.raise_for_status()
    result = response.json()["result"]
    assert result == expected, (result, expected)
