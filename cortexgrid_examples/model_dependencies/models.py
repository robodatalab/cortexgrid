from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import cortexgrid
import requests
from cortexgrid import serve
from fastapi import FastAPI

FAMILY = "Examples"
SQUARE_ROOTED_SUFFIX = "squarerooted"
_LINEAR_SUFFIX = "linear"
_WEIGHTS_FILE = "weights.json"

_linear_app = FastAPI()
_square_rooted_app = FastAPI()


class ServedLinear(cortexgrid.DeploymentClient):
    def predict(self, xs: list[float]) -> list[float]:
        response = requests.post(f"{self.url}/predict", json={"x": xs})
        response.raise_for_status()
        return response.json()["y"]


@serve.ingress(_linear_app)
class Linear:
    @classmethod
    def client(cls, deployment: cortexgrid.Deployment[ServedLinear]) -> ServedLinear:
        return ServedLinear(key=deployment.key, url=deployment.url)

    def __init__(self, deployment: cortexgrid.DeploymentKey) -> None:
        path = cortexgrid.load_model(
            deployment.family, deployment.suffix, deployment.run_name
        )
        weights = json.loads((path / _WEIGHTS_FILE).read_text())
        self._slope = weights["slope"]
        self._intercept = weights["intercept"]

    @_linear_app.post("/predict")
    def predict(self, body: dict[str, list[float]]) -> dict[str, list[float]]:
        return {"y": [self._slope * x + self._intercept for x in body["x"]]}


def linear_weights() -> Path:
    weights_dir = Path(tempfile.mkdtemp())
    (weights_dir / _WEIGHTS_FILE).write_text(
        json.dumps({"slope": 2.0, "intercept": 1.0})
    )
    return weights_dir


class ServedSquareRooted(cortexgrid.DeploymentClient):
    def predict(self, xs: list[float]) -> list[float]:
        response = requests.post(f"{self.url}/predict", json={"x": xs})
        response.raise_for_status()
        return response.json()["y"]


@serve.ingress(_square_rooted_app)
class SquareRooted:
    @classmethod
    def requirements(cls) -> cortexgrid.ModelRequirements:
        return cortexgrid.ModelRequirements(
            models=[
                cortexgrid.DeploymentConfig(
                    family=FAMILY,
                    suffix=_LINEAR_SUFFIX,
                    run_name=cortexgrid.IMPORTED,
                    serve_app=Linear,
                    source=linear_weights,
                )
            ],
        )

    @classmethod
    def client(
        cls, deployment: cortexgrid.Deployment[ServedSquareRooted]
    ) -> ServedSquareRooted:
        return ServedSquareRooted(key=deployment.key, url=deployment.url)

    def __init__(self, deployment: cortexgrid.DeploymentKey) -> None:
        linear = cortexgrid.required_models(deployment)[0]
        self._linear: ServedLinear = linear.client()

    @_square_rooted_app.post("/predict")
    def predict(self, body: dict[str, list[float]]) -> dict[str, list[float]]:
        return {"y": [math.sqrt(y) for y in self._linear.predict(body["x"])]}
