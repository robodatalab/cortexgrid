"""Submit tasks to the cortexflow control plane via MLflow."""

from __future__ import annotations

import cloudpickle  # type: ignore
import json
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from mlflow.tracking import MlflowClient
from pydantic import BaseModel, ConfigDict

from cortexflow.experiment import Experiment


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass
class JobLifecycle:
    status: JobStatus = JobStatus.PENDING
    error: str | None = None
    retry: bool = False
    ray_job_id: str | None = None

    def to_json(self) -> str:
        return json.dumps({"status": self.status.value, **{k: v for k, v in asdict(self).items() if k != "status"}})

    @classmethod
    def from_json(cls, text: str) -> "JobLifecycle":
        data = json.loads(text)
        data["status"] = JobStatus(data["status"])
        return cls(**data)


class Payload(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    experiment: Experiment
    num_gpus: int = 0
    num_cpus: int = 1
    pip_requirements: str = ""


def remote(
    fn: Callable[..., Any],
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    retry: bool = False,
    **kwargs: Any,
) -> str:
    """Submit a function to the control plane. Returns a job ID."""
    experiment = Experiment.get_instance()

    pip_requirements = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    payload = Payload(
        fn=fn,
        args=args,
        kwargs=kwargs,
        experiment=experiment,
        num_gpus=num_gpus,
        num_cpus=num_cpus,
        pip_requirements=pip_requirements,
    )

    lifecycle = JobLifecycle(retry=retry)

    job_id = str(uuid.uuid4())
    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "payload.pkl").write_bytes(cloudpickle.dumps(payload))
    (tmpdir / "lifecycle.json").write_text(lifecycle.to_json())

    artifact_path = f"job/{job_id}"
    client = MlflowClient(tracking_uri=experiment.mlflow_tracking_uri)
    client.log_artifact(experiment.run_id, str(tmpdir / "payload.pkl"), artifact_path=artifact_path)
    client.log_artifact(experiment.run_id, str(tmpdir / "lifecycle.json"), artifact_path=artifact_path)

    return job_id


def get_job_status(experiment: Experiment, job_id: str) -> JobLifecycle:
    """Read the job's lifecycle from MLflow artifacts."""
    client = MlflowClient(tracking_uri=experiment.mlflow_tracking_uri)
    local_path = client.download_artifacts(experiment.run_id, f"job/{job_id}/lifecycle.json")
    return JobLifecycle.from_json(Path(local_path).read_text())
