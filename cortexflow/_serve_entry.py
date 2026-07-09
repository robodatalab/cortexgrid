"""Generic Ray Serve application builder used by every cortexflow.deploy_model.

Ray Serve's REST `import_path` resolves to `cortexflow._serve_entry:build`.
On the cluster replica, `build` imports the serve-app class bundled at
`save_model` time (its import path was stored as an MLflow tag), reads its
`num_gpus`/`num_replicas` class attributes for actor placement, wraps it as a
Ray Serve deployment, and binds it with the (family, suffix, run_name)
identifiers.

The serve-app owns everything about traffic: its own routes, request schemas,
streaming, and timeouts. cortexflow does not interpose a request/response
contract - it only schedules the app and hands it the identifiers it needs to
fetch its own weights via `cortexflow.load_model`.

Design note: resource needs (`num_gpus`/`num_replicas`) are read from plain
class attributes rather than a cortexflow decorator or base class. This is a
deliberate, provisional choice - kept minimal until we see how serve-apps
declare resources in practice; revisit if plain class attributes prove too
limited.
"""

from __future__ import annotations

import importlib
from typing import Any

from ray import serve
from ray.serve.deployment import Application


def build(args: dict[str, Any]) -> Application:
    module_name, class_name = args["class_import_path"].split(":")
    serve_app = getattr(importlib.import_module(module_name), class_name)
    num_gpus = getattr(serve_app, "num_gpus", 0)
    num_replicas = getattr(serve_app, "num_replicas", 1)
    return serve.deployment(serve_app).options(
        num_replicas=num_replicas,
        ray_actor_options={"num_gpus": num_gpus},
    ).bind(args["family"], args["suffix"], args["run_name"])
