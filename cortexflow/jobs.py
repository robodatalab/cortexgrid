"""Submit tasks to the cortexflow control plane via MLflow."""

from __future__ import annotations

import cloudpickle  # type: ignore
from dataclasses import asdict, dataclass
from enum import Enum
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable

from cortexflow.experiment import Experiment, get_mlflow_tracking_uri
from haikunator import Haikunator  # type: ignore
from mlflow.tracking import MlflowClient
from pydantic import BaseModel, ConfigDict


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

PIP_EXTRA_INDEX_URL = os.environ.get(
    "CORTEXFLOW_PIP_EXTRA_INDEX_URL",
    "https://download.pytorch.org/whl/cu128",
)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass
class JobLifecycle:
    experiment: Experiment
    job_id: str
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
        data["experiment"] = Experiment(**data["experiment"])
        return cls(**data)
    
    def save_to_mlflow(self) -> None:
        artifact_path = f"job/{self.job_id}"
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir, "lifecycle.json")
            local_path.write_text(self.to_json())
            client.log_artifact(self.experiment.run_id, str(local_path), artifact_path=artifact_path)

    @classmethod
    def load_from_mlflow(cls, experiment: Experiment, job_id: str) -> "JobLifecycle":
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        local_path = client.download_artifacts(experiment.run_id, f"job/{job_id}/lifecycle.json")
        return cls.from_json(Path(local_path).read_text())


class Payload(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    experiment: Experiment
    job_id: str
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    project_code_root: str
    num_gpus: int = 0
    num_cpus: int = 1

    def save_to_mlflow(self) -> None:
        artifact_path = f"job/{self.job_id}"
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())

        with tempfile.TemporaryDirectory() as tmp_dir:
            Path(tmp_dir, "payload.pkl").write_bytes(cloudpickle.dumps(self))
            shutil.copytree(
                self.project_code_root,
                str(Path(tmp_dir, "project_code_root")),
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*DEFAULT_EXCLUDES),
            )
            client.log_artifacts(self.experiment.run_id, tmp_dir, artifact_path=artifact_path)

    @classmethod
    def load_from_mlflow(cls, experiment: Experiment, job_id: str) -> "Payload":
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        payload_local_path = client.download_artifacts(experiment.run_id, f"job/{job_id}/payload.pkl")
        payload = cloudpickle.loads(Path(payload_local_path).read_bytes())
        payload.project_code_root = client.download_artifacts(experiment.run_id, f"job/{job_id}/project_code_root")

        return payload


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

    name_gen = Haikunator()
    job_id = name_gen.haikunate(token_length=2, token_chars="0123456789")
    project_code_path = _find_pyproject().parent
    payload = Payload(
        experiment=experiment,
        job_id=job_id,
        fn=fn,
        args=args,
        kwargs=kwargs,
        project_code_root=str(project_code_path),
        num_gpus=num_gpus,
        num_cpus=num_cpus,
    )
    payload.save_to_mlflow()

    lifecycle = JobLifecycle(experiment=experiment, job_id=job_id, retry=retry)
    lifecycle.save_to_mlflow()

    return job_id


def _find_pyproject() -> Path:
    """Walk up from cwd() to find pyproject.toml."""
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No pyproject.toml found in any parent directory")


def get_job_status(experiment: Experiment, job_id: str) -> JobLifecycle:
    """Read the job's lifecycle from MLflow artifacts."""
    return JobLifecycle.load_from_mlflow(experiment, job_id)


def list_experiment_jobs(experiment: Experiment) -> list[JobLifecycle]:
    """Return all jobs and their lifecycle states for this experiment+run."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    entries = client.list_artifacts(experiment.run_id, path="job")
    result: list[JobLifecycle] = []
    for entry in entries:
        if not entry.is_dir:
            continue
        job_id = Path(entry.path).name
        result.append(JobLifecycle.load_from_mlflow(experiment, job_id))
    return result

