"""Generic Ray Serve application builder used by every cortexgrid.deploy_model.

Ray Serve's REST `import_path` resolves to `cortexgrid._serve_entry:build`.
On the cluster replica, `build` imports the serve-app class bundled at
`save_model` time (its import path was stored as an MLflow tag), applies Ray's
ingress with the app it was marked with by `cortexgrid.serve.ingress` (again on
each replica, see `_IngressOnReplica`), reads its
`num_gpus`/`num_replicas` class attributes for actor placement, wraps it as a
Ray Serve deployment, and binds it with the (family, suffix, run_name)
identifiers.

The serve-app owns everything about traffic: its own routes, request schemas,
streaming, and timeouts. cortexgrid does not interpose a request/response
contract - it only schedules the app and hands it the identifiers it needs to
fetch its own weights via `cortexgrid.load_model`.

Design note: resource needs (`num_gpus`/`num_replicas`) are read from plain
class attributes rather than a cortexgrid decorator or base class. This is a
deliberate, provisional choice - kept minimal until we see how serve-apps
declare resources in practice; revisit if plain class attributes prove too
limited.
"""

from __future__ import annotations

import importlib
from typing import Any

from ray import serve
from ray.serve.deployment import Application

from cortexgrid.serve import ingress_app


# Requests one replica handles at once before Ray queues the rest.
_MAX_ONGOING_REQUESTS = 100

class _IngressOnReplica:
    """Mixin that re-applies Ray's ingress to `_serve_app` in the replica's own
    process, as Ray creates the replica instance.

    Ray's ingress rewrites the signature of each route method, in place, so
    FastAPI injects the replica instance as `self`. `build` applies it in the
    build process only. The replica imports the serve-app's module afresh, and
    its route methods carry no rewrite. FastAPI < 0.137 analysed routes once,
    in the build process, and the replica received the result. FastAPI >= 0.137
    analyses them in the replica on the first request, and without the rewrite
    reads `self` as a required query parameter (HTTP 422).

    Hooked on `__new__`, not `__init__`: Ray calls `__new__` alone, before the
    serve-app's `__init__`, whether that is sync or async."""

    _serve_app: type

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        # Applied for its side effect on the route methods; the wrapper it
        # returns is not needed.
        serve.ingress(ingress_app(cls._serve_app))(cls._serve_app)
        return super().__new__(cls)


def build(args: dict[str, Any]) -> Application:
    module_name, class_name = args["class_import_path"].split(":")
    serve_app = getattr(importlib.import_module(module_name), class_name)
    # Models saved with a class wrapped by ray.serve.ingress itself carry no
    # mark and are deployed as they are.
    app = ingress_app(serve_app)
    if app is not None:
        on_replica = type(
            serve_app.__name__,
            (_IngressOnReplica, serve_app),
            {"_serve_app": serve_app},
        )
        serve_app = serve.ingress(app)(on_replica)
    num_gpus = getattr(serve_app, "num_gpus", 0)
    num_replicas = getattr(serve_app, "num_replicas", 1)
    return serve.deployment(serve_app).options(
        num_replicas=num_replicas,
        # Ray 2.32 lowered the default from 100 to 5; keep what serve-apps
        # had on Ray 2.9.
        max_ongoing_requests=_MAX_ONGOING_REQUESTS,
        ray_actor_options={"num_gpus": num_gpus},
    ).bind(args["family"], args["suffix"], args["run_name"])
