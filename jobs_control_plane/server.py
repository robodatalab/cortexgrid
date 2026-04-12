"""Jobs control plane — polls MLflow for new submissions and manages their lifecycle on Ray."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import cloudpickle  # type: ignore
from mlflow.tracking import MlflowClient
from ray.job_submission import JobSubmissionClient

from cortexflow.experiment import Experiment, get_mlflow_tracking_uri
from cortexflow.jobs import JobLifecycle, JobStatus, Payload


log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("CORTEXFLOW_POLL_INTERVAL", "5"))

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


def run(experiment: Experiment) -> None:
    """Main loop — poll MLflow, schedule pending jobs, monitor running ones."""
    log.info("Control plane started for experiment=%s run=%s", experiment.experiment_name, experiment.run_id)
    while True:
        try:
            poll_once(experiment)
        except Exception:
            log.exception("Error during poll cycle")
        time.sleep(POLL_INTERVAL_SECONDS)


def poll_once(experiment: Experiment) -> None:
    """Single poll cycle: scan all jobs, act on each based on lifecycle state."""
    mlflow = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    job_dirs = mlflow.list_artifacts(experiment.run_id, path="job")

    for entry in job_dirs:
        if not entry.is_dir:
            continue
        job_id = Path(entry.path).name
        lifecycle = _read_lifecycle(mlflow, experiment.run_id, job_id)

        if lifecycle.status == JobStatus.PENDING:
            _start_job(mlflow, experiment, job_id, lifecycle)
        elif lifecycle.status == JobStatus.RUNNING:
            _check_job(mlflow, experiment, job_id, lifecycle)
        elif lifecycle.status == JobStatus.FAILED and lifecycle.retry:
            log.info("Retrying failed job %s", job_id)
            _start_job(mlflow, experiment, job_id, lifecycle)


def _start_job(
    mlflow: MlflowClient,
    experiment: Experiment,
    job_id: str,
    lifecycle: JobLifecycle,
) -> None:
    payload = _read_payload(mlflow, experiment.run_id, job_id)
    workdir = _build_workdir(payload)

    ray = JobSubmissionClient(experiment.ray_address)
    ray_job_id = ray.submit_job(
        entrypoint="python -m cortexflow._ray_job_driver payload.pkl",
        runtime_env={"working_dir": str(workdir), "pip": str(workdir / "requirements.txt")},
        entrypoint_num_gpus=payload.num_gpus,
        entrypoint_num_cpus=payload.num_cpus,
    )

    lifecycle.status = JobStatus.RUNNING
    lifecycle.ray_job_id = ray_job_id
    lifecycle.error = None
    _write_lifecycle(mlflow, experiment.run_id, job_id, lifecycle)
    log.info("Started job %s as ray_job_id=%s", job_id, ray_job_id)


def _check_job(
    mlflow: MlflowClient,
    experiment: Experiment,
    job_id: str,
    lifecycle: JobLifecycle,
) -> None:
    if lifecycle.ray_job_id is None:
        return

    ray = JobSubmissionClient(experiment.ray_address)
    ray_status = ray.get_job_status(lifecycle.ray_job_id).value

    if ray_status == "SUCCEEDED":
        lifecycle.status = JobStatus.FINISHED
        _write_lifecycle(mlflow, experiment.run_id, job_id, lifecycle)
        log.info("Job %s finished successfully", job_id)
    elif ray_status in ("FAILED", "STOPPED"):
        logs = ray.get_job_logs(lifecycle.ray_job_id)
        lifecycle.status = JobStatus.FAILED
        lifecycle.error = logs[-2000:] if logs else "Unknown error"
        lifecycle.ray_job_id = None
        _write_lifecycle(mlflow, experiment.run_id, job_id, lifecycle)
        log.warning("Job %s failed: %s", job_id, lifecycle.error[:200])


def _read_lifecycle(mlflow: MlflowClient, run_id: str, job_id: str) -> JobLifecycle:
    local_path = mlflow.download_artifacts(run_id, f"job/{job_id}/lifecycle.json")
    return JobLifecycle.from_json(Path(local_path).read_text())


def _write_lifecycle(mlflow: MlflowClient, run_id: str, job_id: str, lifecycle: JobLifecycle) -> None:
    tmpdir = Path(tempfile.mkdtemp())
    path = tmpdir / "lifecycle.json"
    path.write_text(lifecycle.to_json())
    mlflow.log_artifact(run_id, str(path), artifact_path=f"job/{job_id}")


def _read_payload(mlflow: MlflowClient, run_id: str, job_id: str) -> Payload:
    local_path = mlflow.download_artifacts(run_id, f"job/{job_id}/payload.pkl")
    return cloudpickle.loads(Path(local_path).read_bytes())


def _find_pyproject() -> Path:
    """Walk up from cwd() to find pyproject.toml."""
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No pyproject.toml found in any parent directory")


def _build_workdir(payload: Payload) -> Path:
    """Copy project files into a tempdir and add payload + requirements."""
    project_root = _find_pyproject().parent
    workdir = Path(tempfile.mkdtemp(prefix="cortexflow-"))
    shutil.copytree(
        project_root,
        workdir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*DEFAULT_EXCLUDES),
    )
    (workdir / "payload.pkl").write_bytes(cloudpickle.dumps(payload))

    requirements = workdir / "requirements.txt"
    requirements.write_text(
        f"--extra-index-url {PIP_EXTRA_INDEX_URL}\n{payload.pip_requirements}"
    )
    return workdir
