"""Application builder used by cortexflow.deploy_model.

Ray Serve's REST `import_path` must resolve to an already-bound `Application`
or to a builder function taking a single args dict. A bare `Deployment` class
is rejected. `deploy_model` points import_path at `build` here and threads the
user's deployment class through `args`.
"""

from __future__ import annotations

import importlib
from typing import Any

from ray.serve.deployment import Application


def build(args: dict[str, Any]) -> Application:
    module_name, class_name = args["class_import_path"].split(":")
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls.bind(args["family"], args["suffix"], args["run_name"])
