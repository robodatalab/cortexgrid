"""Submit tasks to the cortexflow control plane."""

from __future__ import annotations

import cloudpickle  # type: ignore
from dataclasses import asdict, dataclass, field
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

from cortexflow.experiment import get_mlflow_tracking_uri
from cortexflow.ray_util import (
    list_ray_jobs_with_submission_id,
    ray_submission_id,
    get_ray_job_attempt,
)
from cortexflow.secrets import get_secret
from haikunator import Haikunator  # type: ignore
from mlflow.tracking import MlflowClient
from pydantic import BaseModel, ConfigDict
from tqdm import tqdm  # type: ignore

log = logging.getLogger(__name__)


DEFAULT_EXCLUDES = [
    ".venv",
    ".git",
    "__pycache__",
    "*.pyc",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".DS_Store",
    "*.egg-info",
]

ENABLE_CUDA_ON_RAY = "https://download.pytorch.org/whl/cu128"


@dataclass
class LifecycleEvent:
    """A single observed state on a given attempt of a job.

    ``start`` and ``end`` are ISO 8601 timestamps. ``end`` is ``None``
    while the state is still current; it is set when a subsequent
    observation shows the (attempt, state) pair has changed.
    """

    attempt: int
    state: str
    start: str
    end: str | None = None


@dataclass
class JobLifecycle:
    """Static identity and latches for a job.

    JobLifecycle is the source of truth for *job identity* and for a
    handful of fields that are either immutable or can only change once
    over the lifetime of a job. Live execution status is never stored
    here — it is derived on demand from Ray by :func:`get_ray_job_status`.
    """

    experiment_name: str
    run_id: str
    job_id: str
    stop_requested: bool = False  # latch: False -> True, never cleared
    error: str | None = None  # last worker submission error; overwritten on each attempt
    retry: bool = False  # static flag set at job creation
    history: list[LifecycleEvent] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "JobLifecycle":
        data = json.loads(text)
        data["history"] = [LifecycleEvent(**e) for e in data.get("history", [])]
        return cls(**data)

    def get_ray_job_id(
        self, all_ray_submission_ids: list[str] | None = None
    ) -> str | None:
        if all_ray_submission_ids is None:
            all_ray_submission_ids = list_ray_jobs_with_submission_id()
        prefix = ray_submission_id(self.run_id, self.job_id, None) + "-"
        attempts = [sid for sid in all_ray_submission_ids if sid.startswith(prefix)]
        return max(attempts, key=get_ray_job_attempt) if attempts else None

    def save_to_mlflow(self) -> None:
        artifact_path = f"job/{self.job_id}"
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        log.info(
            "Saving lifecycle for job %s (stop_requested=%s)",
            self.job_id,
            self.stop_requested,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir, "lifecycle.json")
            local_path.write_text(self.to_json())
            client.log_artifact(
                self.run_id, str(local_path), artifact_path=artifact_path
            )

    @classmethod
    def load_from_mlflow(cls, run_id: str, job_id: str) -> "JobLifecycle":
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        local_path = client.download_artifacts(run_id, f"job/{job_id}/lifecycle.json")
        return cls.from_json(Path(local_path).read_text())


class Payload(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    experiment_name: str
    run_id: str
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
        log.info(
            "Uploading payload for job %s from %s", self.job_id, self.project_code_root
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dest = Path(tmp_dir, "project_code_root")
            shutil.copytree(
                self.project_code_root,
                str(project_dest),
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*DEFAULT_EXCLUDES),
            )
            Path(project_dest, "payload.pkl").write_bytes(cloudpickle.dumps(self))
            pip_requirements = subprocess.run(
                [sys.executable, "-m", "pip", "freeze"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            pip_requirements = _strip_ray(pip_requirements)
            pip_requirements = _inject_github_token(pip_requirements)
            (project_dest / "requirements.txt").write_text(
                f"--extra-index-url {ENABLE_CUDA_ON_RAY}\n{pip_requirements}"
            )
            _upload_dir(client, self.run_id, tmp_dir, artifact_path)
            log.info("Payload upload complete for job %s", self.job_id)

    @classmethod
    def load_from_mlflow(cls, run_id: str, job_id: str) -> "Payload":
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        log.info("Downloading payload for job %s", job_id)
        project_code_root = client.download_artifacts(
            run_id, f"job/{job_id}/project_code_root"
        )
        payload = cloudpickle.loads(Path(project_code_root, "payload.pkl").read_bytes())
        payload.project_code_root = project_code_root
        log.info(
            "Payload downloaded for job %s, project_code_root=%s",
            job_id,
            payload.project_code_root,
        )

        return payload


def schedule_remote_job(
    experiment_name: str,
    run_id: str,
    fn: Callable[..., Any],
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    retry: bool = False,
    **kwargs: Any,
) -> str:
    """Submit a function to the control plane. Returns a job ID."""
    name_gen = Haikunator()
    job_id = name_gen.haikunate(token_length=2, token_chars="0123456789")
    project_code_path = _find_pyproject().parent
    log.info("Submitting job %s (project=%s)", job_id, project_code_path)
    payload = Payload(
        experiment_name=experiment_name,
        run_id=run_id,
        job_id=job_id,
        fn=fn,
        args=args,
        kwargs=kwargs,
        project_code_root=str(project_code_path),
        num_gpus=num_gpus,
        num_cpus=num_cpus,
    )
    payload.save_to_mlflow()

    lifecycle = JobLifecycle(
        experiment_name=experiment_name, run_id=run_id, job_id=job_id, retry=retry
    )
    lifecycle.save_to_mlflow()

    return job_id


def _find_pyproject() -> Path:
    """Walk up from cwd() to find pyproject.toml."""
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No pyproject.toml found in any parent directory")


def _strip_ray(pip_requirements: str) -> str:
    """Drop ray from pip freeze output. Ray rejects any runtime_env pip list
    that would install a different ray version than the cluster is running."""
    result = []
    for line in pip_requirements.splitlines():
        name = line.lstrip().split("==", 1)[0].split(" @", 1)[0].split("[", 1)[0]
        if name.strip().lower() == "ray":
            continue
        result.append(line)
    return "\n".join(result)


def _inject_github_token(pip_requirements: str) -> str:
    """Rewrite github.com git URLs in pip freeze output to include the auth token."""
    token = get_secret("GH_TOKEN")
    return pip_requirements.replace(
        "git+https://github.com/",
        f"git+https://x-access-token:{token}@github.com/",
    )


def _upload_dir(
    client: MlflowClient, run_id: str, local_dir: str, artifact_path: str
) -> None:
    """Upload a directory to MLflow with a per-file progress bar."""
    files = [(root, f) for root, _, filenames in os.walk(local_dir) for f in filenames]
    total_bytes = sum(os.path.getsize(os.path.join(r, f)) for r, f in files)
    with tqdm(total=total_bytes, unit="B", unit_scale=True, desc="Uploading") as pbar:
        for root, filename in files:
            filepath = os.path.join(root, filename)
            rel_dir = os.path.relpath(root, local_dir)
            dest = f"{artifact_path}/{rel_dir}" if rel_dir != "." else artifact_path
            client.log_artifact(run_id, filepath, artifact_path=dest)
            pbar.update(os.path.getsize(filepath))


def list_experiment_run_jobs(run_id: str) -> list[JobLifecycle]:
    """Return all jobs and their lifecycle states for this experiment+run."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    entries = client.list_artifacts(run_id, path="job")
    result: list[JobLifecycle] = []
    for entry in entries:
        if not entry.is_dir:
            continue
        job_id = Path(entry.path).name
        try:
            result.append(JobLifecycle.load_from_mlflow(run_id, job_id))
        except Exception:
            logging.getLogger(__name__).warning(
                "Skipping job %s: missing lifecycle", job_id
            )
    return result


def stop_experiment_run_jobs(run_id: str) -> None:
    """Request all jobs in the run to stop by flipping the stop_requested latch.

    This function never touches Ray. The control plane observes the
    latch on its next poll and calls `ray.stop_job` for any job that
    has reached Ray. For jobs that have not yet been submitted, the
    latch short-circuits the submission path in the worker.

    Idempotent: already-requested jobs are skipped, and the flag has
    no effect on jobs that Ray already reports as terminal.
    """
    for job in list_experiment_run_jobs(run_id):
        if job.stop_requested:
            continue
        job.stop_requested = True
        job.save_to_mlflow()
