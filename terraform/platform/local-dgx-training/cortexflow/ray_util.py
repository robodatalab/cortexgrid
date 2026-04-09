"""Ray wrappers.

cortexflow.remote — decorator that wraps @ray.remote, auto-builds runtime_env
                    from pyproject.toml, and injects service env vars
cortexflow.get    — ray.get with the same signature
"""

from __future__ import annotations

from typing import Any

import ray

from cortexflow.config import get_config
from cortexflow.project import build_runtime_env


def remote(
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    max_retries: int = 0,
    retry_exceptions: bool = False,
    **kwargs: Any,
) -> Any:
    """Decorator wrapping @ray.remote that auto-configures the runtime.

    Reads the project's pyproject.toml to build the runtime_env:
    - working_dir: project root
    - pip: dependencies from [project.dependencies] + [tool.uv.sources]
    - excludes: .venv/, .git/, __pycache__/, etc.
    - env_vars: MLflow, S3 credentials (so task code can call cortexflow.init())

    Usage:
        @cortexflow.remote(num_gpus=1, max_retries=3)
        def train_step(batch):
            ...

        # or inline
        train_fn = cortexflow.remote(num_gpus=1)(my_function)
    """
    ray_kwargs: dict[str, Any] = {
        "num_gpus": num_gpus,
        "num_cpus": num_cpus,
        "max_retries": max_retries,
        "retry_exceptions": retry_exceptions,
    }
    ray_kwargs.update(kwargs)

    runtime_env = build_runtime_env()

    config = get_config()
    env_vars = config.env_vars_for_job()
    if env_vars:
        existing = runtime_env.get("env_vars", {})
        existing.update(env_vars)
        runtime_env["env_vars"] = existing

    ray_kwargs["runtime_env"] = runtime_env

    def decorator(fn: Any) -> Any:
        return ray.remote(**ray_kwargs)(fn)

    if len(args) == 1 and callable(args[0]) and not kwargs:
        rt = build_runtime_env()
        env = get_config().env_vars_for_job()
        if env:
            rt.setdefault("env_vars", {}).update(env)
        return ray.remote(runtime_env=rt)(args[0])

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
