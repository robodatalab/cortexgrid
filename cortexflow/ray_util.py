"""Submit functions to the Ray cluster as jobs."""

from __future__ import annotations

import cloudpickle  # type: ignore
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

    workdir = Path(tempfile.mkdtemp(prefix="cortexflow-"))
    (workdir / "payload.pkl").write_bytes(cloudpickle.dumps(payload))

    pip = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    client = JobSubmissionClient(experiment.ray_address)
    job_id = client.submit_job(
        entrypoint="python -m cortexflow._ray_job_driver payload.pkl",
        runtime_env={"working_dir": str(workdir), "pip": pip},
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
