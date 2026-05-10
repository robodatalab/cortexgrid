"""Cortexflow wrappers around Ray Serve for deploying models from the registry.

deploy/undeploy/list_deployments use the Ray Serve Python API directly, so the
process making the call must be connected to the Ray cluster:
    in-cluster: ray.init(address="auto") works automatically
    laptop:     export RAY_ADDRESS=ray://<head>:10001 first

Naming: the Ray Serve application is named "<family>__<suffix>__<run_name>".
This relies on family/suffix/run_name not containing the literal "__".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import ray
from ray import serve
from ray.serve.deployment import Deployment as RayDeployment

from cortexflow.infra import get_ray_serve_uri


@dataclass
class Deployment:
    family: str
    suffix: str
    run_name: str
    url: str
    status: str


def _app_name(family: str, suffix: str, run_name: str) -> str:
    return f"{family}__{suffix}__{run_name}"


def _route_prefix(family: str, suffix: str, run_name: str) -> str:
    return f"/r/{family}/{suffix}/{run_name}"


def _connect() -> None:
    if not ray.is_initialized():
        ray.init(address="auto", ignore_reinit_error=True)


def model_deployment(
    num_gpus: int = 1,
    num_replicas: int = 1,
    **kw: Any,
) -> Callable[[Callable], RayDeployment]:
    """serve.deployment configured for cortexflow infra."""
    actor_options = {"num_gpus": num_gpus, **kw.pop("ray_actor_options", {})}
    return serve.deployment(
        num_replicas=num_replicas,
        ray_actor_options=actor_options,
        **kw,
    )


def deploy_model(
    cls: type | RayDeployment, family: str, suffix: str, run_name: str
) -> Deployment:
    """Schedule a Ray Serve app bound to (family, suffix, run_name).

    `cls` must be a class decorated with @model_deployment (or @serve.deployment).
    The union accepts both views of the decorated value: pyright tracks the
    transformation and sees a Deployment, mypy keeps the original `type`.
    """
    _connect()
    name = _app_name(family, suffix, run_name)
    route = _route_prefix(family, suffix, run_name)
    bound = cls.bind(family, suffix, run_name)  # type: ignore[union-attr]
    serve.run(bound, name=name, route_prefix=route)
    return Deployment(
        family=family,
        suffix=suffix,
        run_name=run_name,
        url=f"{get_ray_serve_uri()}{route}",
        status="running",
    )


def undeploy_model(family: str, suffix: str, run_name: str) -> None:
    """Tear down the Ray Serve app for this model."""
    _connect()
    serve.delete(_app_name(family, suffix, run_name))


def list_deployed_models() -> list[Deployment]:
    """Return Deployment records for every Ray Serve app whose name matches our scheme."""
    _connect()
    base = get_ray_serve_uri()
    result: list[Deployment] = []
    for app_name, app_status in serve.status().applications.items():
        parts = app_name.split("__")
        if len(parts) != 3:
            continue
        family, suffix, run_name = parts
        result.append(
            Deployment(
                family=family,
                suffix=suffix,
                run_name=run_name,
                url=f"{base}{_route_prefix(family, suffix, run_name)}",
                status=str(app_status.status),
            )
        )
    return result
