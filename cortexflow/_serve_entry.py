"""Generic Ray Serve wrapper used by every cortexflow.deploy_model.

Ray Serve's REST `import_path` resolves to `cortexflow._serve_entry:build`.
On the cluster replica, `build` imports the user's plain class (the import
path was captured at save_model time and stored as an MLflow tag), reads
its `num_gpus`/`num_replicas` class attributes, and binds an
`_InferenceWrapper` that calls `cortexflow.load_model(...)` to reconstruct
the model and exposes a single `POST /infer` endpoint.
"""

from __future__ import annotations

import importlib
from typing import Any

from fastapi import FastAPI
from ray import serve
from ray.serve.deployment import Application

import cortexflow


_app = FastAPI()


@serve.deployment
@serve.ingress(_app)
class _InferenceWrapper:
    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        self._model = cortexflow.load_model(family, suffix, run_name)

    @_app.post("/infer")
    async def infer(self, body: dict[str, Any]) -> dict[str, Any]:
        args = body.get("args", [])
        kwargs = body.get("kwargs", {})
        return {"result": self._model.infer(*args, **kwargs)}


def build(args: dict[str, Any]) -> Application:
    module_name, class_name = args["class_import_path"].split(":")
    user_cls = getattr(importlib.import_module(module_name), class_name)
    num_gpus = getattr(user_cls, "num_gpus", 0)
    num_replicas = getattr(user_cls, "num_replicas", 1)
    return _InferenceWrapper.options(  # type: ignore
        num_replicas=num_replicas,
        ray_actor_options={"num_gpus": num_gpus},
    ).bind(args["family"], args["suffix"], args["run_name"])
