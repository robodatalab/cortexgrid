"""Jobs control plane — polls MLflow for new submissions and manages their lifecycle on Ray."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import cloudpickle  # type: ignore
from ray.job_submission import JobSubmissionClient

from cortexflow.experiment import Experiment, list_experiments, get_ray_address, set_runs_on_dgx
from cortexflow.jobs import JobLifecycle, JobStatus, Payload, list_experiment_jobs


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


def poll_once() -> None:
    """Single poll cycle: scan all jobs, act on each based on lifecycle state."""
    experiments = list_experiments()
    for experiment in experiments:
        jobs = list_experiment_jobs(experiment)
        for job in jobs:
            if job.status == JobStatus.PENDING:
                _start_job(experiment, job)
            elif job.status == JobStatus.RUNNING:
                _check_job(experiment, job)
            elif job.status == JobStatus.FAILED and job.retry:
                log.info("Retrying failed job %s", job.ray_job_id)
                _start_job(experiment, job)


def _start_job(
    experiment: Experiment,
    job: JobLifecycle,
) -> None:
    payload = Payload.load_from_mlflow(experiment, job.job_id)
    workdir = _build_workdir(payload)

    ray = JobSubmissionClient(get_ray_address())
    ray_job_id = ray.submit_job(
        entrypoint="python -m cortexflow._ray_job_driver payload.pkl",
        runtime_env={"working_dir": str(workdir), "pip": str(workdir / "requirements.txt")},
        entrypoint_num_gpus=payload.num_gpus,
        entrypoint_num_cpus=payload.num_cpus,
    )

    lifecycle = JobLifecycle.load_from_mlflow(experiment, job.job_id)
    lifecycle.status = JobStatus.RUNNING
    lifecycle.ray_job_id = ray_job_id
    lifecycle.error = None
    lifecycle.save_to_mlflow()
    log.info("Started job %s as ray_job_id=%s", job.job_id, ray_job_id)


def _check_job(
    experiment: Experiment,
    job: JobLifecycle,
) -> None:
    lifecycle = JobLifecycle.load_from_mlflow(experiment, job.job_id)
    if lifecycle.ray_job_id is None:
        # the job hasn't been scheduled yet - this case should not be entered
        raise AssertionError(f"The job {job} hasn't been scheduled yet - this case should not be entered")

    ray = JobSubmissionClient(get_ray_address())
    ray_status = ray.get_job_status(lifecycle.ray_job_id).value

    if ray_status == "SUCCEEDED":
        lifecycle.status = JobStatus.FINISHED
        lifecycle.save_to_mlflow()
        log.info("Job %s finished successfully", lifecycle.job_id)
    elif ray_status in ("FAILED", "STOPPED"):
        logs = ray.get_job_logs(lifecycle.ray_job_id)
        lifecycle.status = JobStatus.FAILED
        lifecycle.error = logs[-2000:] if logs else "Unknown error"
        lifecycle.ray_job_id = None
        lifecycle.save_to_mlflow()
        log.warning("Job %s failed: %s", lifecycle.job_id, lifecycle.error[:200])



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


def main() -> None:
    set_runs_on_dgx(True)
    while True:
        try:
            poll_once()
        except Exception:
            log.exception("Error during poll cycle")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()

