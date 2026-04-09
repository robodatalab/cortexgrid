"""Ray wrappers.

cortexflow.remote — decorator that wraps @ray.remote and auto-injects env vars
cortexflow.get    — ray.get with the same signature
"""

from __future__ import annotations

from typing import Any

import ray

from cortexflow.config import get_config


def remote(
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    max_retries: int = 0,
    retry_exceptions: bool = False,
    **kwargs: Any,
) -> Any:
    """Decorator wrapping @ray.remote that injects env vars into the runtime.

    Usage:
        @cortexflow.remote(num_gpus=1, max_retries=3)
        def train_step(batch):
            # MLflow, S3 env vars are available here automatically
            ...
    """
    ray_kwargs: dict[str, Any] = {
        "num_gpus": num_gpus,
        "num_cpus": num_cpus,
        "max_retries": max_retries,
        "retry_exceptions": retry_exceptions,
        **kwargs,
    }

    config = get_config()
    env_vars = config.env_vars_for_job()

    runtime_env = ray_kwargs.pop("runtime_env", {})
    if env_vars:
        existing = runtime_env.get("env_vars", {})
        existing.update(env_vars)
        runtime_env["env_vars"] = existing
    if runtime_env:
        ray_kwargs["runtime_env"] = runtime_env

    def decorator(fn: Any) -> Any:
        return ray.remote(**ray_kwargs)(fn)

    # Support both @cortexflow.remote and @cortexflow.remote(num_gpus=1)
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return ray.remote(args[0])
    return decorator


def get(futures: Any, **kwargs: Any) -> Any:
    """Alias for ray.get()."""
    return ray.get(futures, **kwargs)


def get_ray_client() -> Any:
    """Return a Ray JobSubmissionClient connected to the cluster."""
    from ray.job_submission import JobSubmissionClient

    config = get_config()
    if not config.ray_address:
        raise RuntimeError("RAY_ADDRESS not configured. Run cortexflow.init() first.")
    return JobSubmissionClient(config.ray_address)
