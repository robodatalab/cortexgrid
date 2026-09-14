"""Test fixtures for the model_serving integration tests.

Under the current cortexflow contract the stored model and the serve-app are
separate objects:

  - the stored "weights" are an opaque directory - here a single weights.json
    holding one integer constant, the kind of artifact a training job produces
    before `cortexflow.save_model(weights_dir, ...)`;
  - the serve-app is a Ray Serve ingress class that downloads that directory at
    startup via `cortexflow.load_model` and exposes its own route.

`contact_deployment` exercises the deploy-then-call path end-to-end and lives
at module level so `cortexflow.remote()` can ship it to a Ray worker.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests
from fastapi import FastAPI
from ray import serve

import cortexgrid


_CONSTANT_FILE = "weights.json"


def write_weights(d: Path, constant: int) -> None:
    """Write the stub's 'trained weights' (a single integer) into directory `d`,
    the way a training job would before `cortexflow.save_model`."""
    (d / _CONSTANT_FILE).write_text(json.dumps({"constant": constant}))


def read_constant(weights_dir: Path) -> int:
    """Read the constant back out of a weights directory."""
    return json.loads((weights_dir / _CONSTANT_FILE).read_text())["constant"]


_app = FastAPI()


@serve.ingress(_app)
class AddConstantServeApp:
    """Serve-app fronting the stub weights. Loads the constant from the weights
    directory at startup and returns `x + constant` on its own POST /add route.

    Declares its Ray Serve resources as plain class attributes (num_gpus /
    num_replicas), which `cortexflow._serve_entry.build` reads at bind time."""

    num_gpus = 0
    num_replicas = 1

    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        self._constant = read_constant(cortexgrid.load_model(family, suffix, run_name))

    @_app.post("/add")
    async def add(self, body: dict) -> dict:
        return {"result": body["x"] + self._constant}


def contact_deployment(
    family: str, suffix: str, run_name: str, x: int, expected: int
) -> None:
    """Re-resolve a deployment by triple, POST to its route, assert the answer.

    Lives at module level so `cortexflow.remote()` can ship it to a Ray worker.
    Builds its own HTTP client against the Deployment URL - cortexflow no longer
    provides an inference proxy."""
    deployed = cortexgrid.deploy_model(family, suffix, run_name)
    response = requests.post(f"{deployed.url}/add", json={"x": x}, timeout=60)
    response.raise_for_status()
    result = response.json()["result"]
    assert result == expected, (result, expected)
