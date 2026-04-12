"""Submit tasks to the cortexflow control plane via MLflow."""

from __future__ import annotations

import cloudpickle  # type: ignore
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from mlflow.tracking import MlflowClient
from pydantic import BaseModel, ConfigDict
from ray.job_submission import JobSubmissionClient

from cortexflow.experiment import Experiment


# DGX Spark is Grace+Blackwell (sm_100). Default PyPI ships only CPU torch
# wheels for Linux aarch64; pointing pip at NVIDIA's cu128 index makes torch
# resolve to the CUDA+Blackwell wheel. Overridable for other hardware.
PIP_EXTRA_INDEX_URL = os.environ.get(
    "CORTEXFLOW_PIP_EXTRA_INDEX_URL",
    "https://download.pytorch.org/whl/cu128",
)


DEFAULT_EXCLUDES = [
    ".venv",
    ".git",
    "__pycache__",
    "*.pyc",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
]


def _find_pyproject() -> Path:
    """Walk up from cwd() to find pyproject.toml. Raises if none found."""
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No pyproject.toml found in any parent directory")


class Payload(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    experiment: Experiment


class Job:
    def __init__(self, client: JobSubmissionClient, job_id: str) -> None:
        self.client = client
        self.job_id = job_id


def remote(
    fn: Callable[..., Any],
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    **kwargs: Any,
) -> Job:
    experiment = Experiment.get_instance()
    payload = Payload(fn=fn, args=args, kwargs=kwargs, experiment=experiment)

    project_root = _find_pyproject().parent
    workdir = Path(tempfile.mkdtemp(prefix="cortexflow-"))
    shutil.copytree(
        project_root,
        workdir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*DEFAULT_EXCLUDES),
    )
    payload_bytes = cloudpickle.dumps(payload)
    (workdir / "payload.pkl").write_bytes(payload_bytes)

    freeze_output = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    requirements = workdir / "requirements.txt"
    requirements.write_text(
        f"--extra-index-url {PIP_EXTRA_INDEX_URL}\n{freeze_output}"
    )

    client = JobSubmissionClient(experiment.ray_address)
    job_id = client.submit_job(
        entrypoint="python -m cortexflow._ray_job_driver payload.pkl",
        runtime_env={"working_dir": str(workdir), "pip": str(requirements)},
        entrypoint_num_gpus=num_gpus,
        entrypoint_num_cpus=num_cpus,
    )
    _register_ray_job(experiment, job_id, payload_bytes)
    return Job(client=client, job_id=job_id)


def _register_ray_job(
    experiment: Experiment, ray_job_id: str, payload_bytes: bytes
) -> None:
    payload_file = Path(tempfile.mkdtemp()) / ray_job_id
    payload_file.write_bytes(payload_bytes)
    client = MlflowClient(tracking_uri=experiment.mlflow_tracking_uri)
    client.log_artifact(experiment.run_id, str(payload_file), artifact_path="ray-job")
