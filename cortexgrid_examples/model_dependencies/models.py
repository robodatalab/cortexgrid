from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import cortexgrid
from cortexgrid import serve

FAMILY = "Examples"
SQUARE_ROOTED_SUFFIX = "squarerooted"
_LINEAR_SUFFIX = "linear"
_WEIGHTS_FILE = "weights.json"


@serve.ingress
class Linear:
    def __init__(self, deployment: cortexgrid.DeploymentKey) -> None:
        path = cortexgrid.load_model(
            deployment.family, deployment.suffix, deployment.run_name
        )
        weights = json.loads((path / _WEIGHTS_FILE).read_text())
        self._slope = weights["slope"]
        self._intercept = weights["intercept"]

    @serve.endpoint
    def predict(self, xs: list[float]) -> list[float]:
        return [self._slope * x + self._intercept for x in xs]


def linear_weights() -> Path:
    weights_dir = Path(tempfile.mkdtemp())
    (weights_dir / _WEIGHTS_FILE).write_text(
        json.dumps({"slope": 2.0, "intercept": 1.0})
    )
    return weights_dir


@serve.ingress
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

    def __init__(self, deployment: cortexgrid.DeploymentKey) -> None:
        linear = cortexgrid.required_models(deployment)[0]
        self._linear: Linear = linear.client()

    @serve.endpoint
    def predict(self, xs: list[float]) -> list[float]:
        return [math.sqrt(y) for y in self._linear.predict(xs)]
