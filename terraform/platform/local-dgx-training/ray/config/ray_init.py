"""Shared Ray initialization helper for job scripts."""

from __future__ import annotations

import os
from pathlib import Path

import ray
import yaml
from dotenv import load_dotenv


def init_ray(
    runtime_env_name: str | None = None,
    extra_pip: list[str] | None = None,
    extra_env_vars: dict[str, str] | None = None,
) -> None:
    """Initialize Ray connection with optional runtime environment.

    Args:
        runtime_env_name: Name of a YAML file in ray/runtime_envs/ (without extension).
        extra_pip: Additional pip packages to install on top of the runtime env.
        extra_env_vars: Extra environment variables to inject into the Ray runtime.
    """
    # Load .env from the repo root (local-dgx-training/)
    repo_root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(repo_root / ".env")

    ray_address = os.environ.get("RAY_ADDRESS")
    runtime_env: dict | None = None

    if runtime_env_name:
        env_file = repo_root / "ray" / "runtime_envs" / f"{runtime_env_name}.yaml"
        if env_file.exists():
            with open(env_file) as f:
                runtime_env = yaml.safe_load(f)
        else:
            raise FileNotFoundError(f"Runtime env not found: {env_file}")

        if extra_pip:
            runtime_env.setdefault("pip", []).extend(extra_pip)

        # Inject MLflow/storage env vars so jobs can reach the tracking server
        env_vars = {
            "MLFLOW_TRACKING_URI": os.environ.get("MLFLOW_TRACKING_URI", ""),
            "MLFLOW_S3_ENDPOINT_URL": os.environ.get("ARTIFACT_STORE_ENDPOINT", ""),
            "AWS_ACCESS_KEY_ID": os.environ.get("ARTIFACT_STORE_ACCESS_KEY", ""),
            "AWS_SECRET_ACCESS_KEY": os.environ.get("ARTIFACT_STORE_SECRET_KEY", ""),
        }
        if extra_env_vars:
            env_vars.update(extra_env_vars)
        runtime_env["env_vars"] = env_vars

    ray.init(address=ray_address, runtime_env=runtime_env)


def get_mlflow_env_vars() -> dict[str, str]:
    """Return a dict of MLflow-related env vars for injection into Ray jobs."""
    load_dotenv()
    return {
        "MLFLOW_TRACKING_URI": os.environ.get("MLFLOW_TRACKING_URI", ""),
        "MLFLOW_S3_ENDPOINT_URL": os.environ.get("ARTIFACT_STORE_ENDPOINT", ""),
        "AWS_ACCESS_KEY_ID": os.environ.get("ARTIFACT_STORE_ACCESS_KEY", ""),
        "AWS_SECRET_ACCESS_KEY": os.environ.get("ARTIFACT_STORE_SECRET_KEY", ""),
    }
