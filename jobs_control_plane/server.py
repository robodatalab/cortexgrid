"""Jobs control plane — polls jobs db (atm. MLFlow) for new submissions and manages their lifecycle on Ray."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from ray.job_submission import JobSubmissionClient

from cortexflow import (
    Experiment,
    list_experiments,
    get_ray_job_server_uri,
    JobLifecycle,
    JobStatus,
    Payload,
    list_experiment_run_jobs,
    set_runs_on_server,
)


log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("CORTEXFLOW_POLL_INTERVAL", "5"))
HEARTBEAT_PATH = Path("/tmp/cp_heartbeat")


def poll_once() -> None:
    """Single poll cycle: scan all jobs, act on each based on lifecycle state."""
    experiments = list_experiments()
    log.info("Poll: found %d experiment(s)", len(experiments))
    for experiment in experiments:
        jobs = list_experiment_run_jobs(experiment.run_id)
        log.info(
            "  %s/%s: %d job(s)",
            experiment.experiment_name,
            experiment.run_id,
            len(jobs),
        )
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
    log.info(
        "Starting a job %s from experiment %s/%s",
        job.job_id,
        experiment.experiment_name,
        experiment.run_name,
    )

    payload = Payload.load_from_mlflow(experiment.run_id, job.job_id)

    log.info(
        "Payload for job %s from experiment %s/%s loaded",
        job.job_id,
        experiment.experiment_name,
        experiment.run_name,
    )

    ray = JobSubmissionClient(get_ray_job_server_uri())
    ray_job_id = ray.submit_job(
        entrypoint="python -m cortexflow._ray_job_driver payload.pkl",
        runtime_env={
            "working_dir": payload.project_code_root,
            "pip": str(Path(payload.project_code_root) / "requirements.txt"),
        },
        entrypoint_num_gpus=payload.num_gpus,
        entrypoint_num_cpus=payload.num_cpus,
    )

    lifecycle = JobLifecycle.load_from_mlflow(experiment.run_id, job.job_id)
    lifecycle.status = JobStatus.RUNNING
    lifecycle.ray_job_id = ray_job_id
    lifecycle.error = None

    log.info(
        "Saving job %s from experiment %s/%s lifecycle",
        job.job_id,
        experiment.experiment_name,
        experiment.run_name,
    )
    lifecycle.save_to_mlflow()
    log.info("Started job %s as ray_job_id=%s", job.job_id, ray_job_id)


def _check_job(
    experiment: Experiment,
    job: JobLifecycle,
) -> None:
    log.info(
        "Checking a job %s from experiment %s/%s",
        job.job_id,
        experiment.experiment_name,
        experiment.run_name,
    )

    lifecycle = JobLifecycle.load_from_mlflow(experiment.run_id, job.job_id)
    if lifecycle.ray_job_id is None:
        # the job hasn't been scheduled yet - this case should not be entered
        log.info(
            "The job %s, experiment %s/%s hasn't been scheduled yet - this case should not be entered",
            job.job_id,
            experiment.experiment_name,
            experiment.run_name,
        )
        raise AssertionError(
            f"The job {job} hasn't been scheduled yet - this case should not be entered"
        )

    ray = JobSubmissionClient(get_ray_job_server_uri())
    ray_status = ray.get_job_status(lifecycle.ray_job_id).value

    if ray_status == "SUCCEEDED":
        lifecycle.status = JobStatus.FINISHED
        lifecycle.save_to_mlflow()
        log.info("Job %s finished successfully", lifecycle.job_id)
    elif ray_status in ("FAILED", "STOPPED"):
        logs = ray.get_job_logs(lifecycle.ray_job_id)
        lifecycle.status = JobStatus.FAILED
        lifecycle.error = logs[-2000:] if logs else "Unknown error"
        lifecycle.save_to_mlflow()
        log.warning("Job %s failed: %s", lifecycle.job_id, lifecycle.error[:200])


def main() -> None:
    set_runs_on_server(True)
    while True:
        try:
            poll_once()
            HEARTBEAT_PATH.touch()
        except Exception:
            log.exception("Error during poll cycle")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
