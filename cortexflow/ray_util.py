"""Submit functions to the Ray cluster as jobs."""

from __future__ import annotations

import cloudpickle  # type: ignore
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
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


@dataclass
class Job:
    client: JobSubmissionClient
    job_id: str


def remote(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Job:
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
    (workdir / "payload.pkl").write_bytes(cloudpickle.dumps(payload))

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
    )
    _register_ray_job(experiment, job_id)
    return Job(client=client, job_id=job_id)


def _register_ray_job(experiment: Experiment, ray_job_id: str) -> None:
    marker = Path(tempfile.mkdtemp()) / ray_job_id
    marker.touch()
    client = MlflowClient(tracking_uri=experiment.mlflow_tracking_uri)
    client.log_artifact(experiment.run_id, str(marker), artifact_path="ray-job")


def get_ray_status(experiment: Experiment, ray_job_id: str) -> str:
    """Return the current status of a previously submitted ray job."""
    client = JobSubmissionClient(experiment.ray_address)
    return client.get_job_status(ray_job_id).value


def get_ray_logs(experiment: Experiment, ray_job_id: str) -> str:
    """Return the stdout/stderr of a previously submitted ray job."""
    client = JobSubmissionClient(experiment.ray_address)
    return client.get_job_logs(ray_job_id)
