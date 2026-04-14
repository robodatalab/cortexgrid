"""Jobs control plane — polls MLflow for pending jobs and submits them to Ray.

Post-pivot architecture:

- ``JobLifecycle`` is pure static identity + latches (``ray_job_id``,
  ``stop_requested``). Status is never persisted.
- The control plane uses Ray as the source of truth for execution state.
- Submission to Ray is async — handed to a ``ProcessPoolExecutor`` so a
  large payload upload cannot block the poll loop.
- Submission is idempotent via a deterministic ``submission_id`` derived
  from ``(run_id, job_id)``; a crashed worker can be re-run safely.
- A single heartbeat file is touched at the end of every successful
  poll cycle. The Docker healthcheck watches its mtime.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

from cortexflow import (
    JobLifecycle,
    Payload,
    get_ray_status,
    list_experiment_run_jobs,
    list_experiments,
    set_runs_on_server,
    stop_ray_job,
    submit_ray_job,
)


log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("CORTEXFLOW_POLL_INTERVAL", "5"))
STARTER_WORKERS = int(os.environ.get("CORTEXFLOW_STARTER_WORKERS", "4"))
HEARTBEAT_PATH = Path("/tmp/cp_heartbeat")


def _ray_submission_id(run_id: str, job_id: str) -> str:
    """Deterministic Ray submission id derived from a job's identity.

    Making this a pure function of (run_id, job_id) is the hinge that
    lets the worker re-attempt submission after a crash without ever
    creating a duplicate Ray job: the second attempt hits Ray's "already
    exists" check and is short-circuited.
    """
    return f"{run_id}-{job_id}"


def _submit_if_absent(submission_id: str, payload: Payload) -> None:
    """Submit the job to Ray unless Ray already has this submission_id."""
    try:
        get_ray_status(submission_id)
        return  # already there — previous attempt reached Ray
    except Exception:
        pass
    submit_ray_job(
        submission_id=submission_id,
        entrypoint="python -m cortexflow._ray_job_driver payload.pkl",
        runtime_env={
            "working_dir": payload.project_code_root,
            "pip": str(Path(payload.project_code_root) / "requirements.txt"),
        },
        num_gpus=payload.num_gpus,
        num_cpus=payload.num_cpus,
    )


def _submit_job_worker(run_id: str, job_id: str) -> None:
    """Async submission body run inside a ProcessPoolExecutor subprocess.

    Never raises. On any failure, logs and returns without mutating the
    lifecycle; the next poll cycle sees ray_job_id is still None and
    re-dispatches. Because the submission_id is deterministic, Ray will
    reject a duplicate submission on the retry and we proceed to save
    ray_job_id on the lifecycle.
    """
    set_runs_on_server(True)
    try:
        lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)
    except Exception:
        log.exception("Worker cannot load lifecycle for job %s", job_id)
        return
    if lifecycle.stop_requested:
        log.info("Worker skipping job %s: stop_requested is set", job_id)
        return
    submission_id = _ray_submission_id(run_id, job_id)
    try:
        payload = Payload.load_from_mlflow(run_id, job_id)
        _submit_if_absent(submission_id, payload)
    except Exception:
        log.exception("Worker failed to submit job %s to Ray", job_id)
        return
    lifecycle.ray_job_id = submission_id
    try:
        lifecycle.save_to_mlflow()
    except Exception:
        log.exception(
            "Worker submitted job %s to Ray but could not persist ray_job_id",
            job_id,
        )


def _dispatch_job(
    executor: ProcessPoolExecutor,
    in_flight: dict[str, Future],
    job: JobLifecycle,
) -> None:
    """Single-job decision step called from poll_once.

    The in-flight key is the same deterministic string we use for the
    Ray submission_id — one canonical name per job identity.
    """
    key = _ray_submission_id(job.run_id, job.job_id)
    if job.ray_job_id is None:
        if job.stop_requested:
            return  # effectively terminal; nothing to do
        if key in in_flight:
            return  # a worker is already handling this job
        log.info("Dispatching job %s to worker pool", key)
        in_flight[key] = executor.submit(_submit_job_worker, job.run_id, job.job_id)
        return

    # At this point we know that the job has been scheduled with Ray

    if not job.stop_requested:
        return  # Ray owns it and no stop was requested — no-op

    # At this point we know that the use wants to stop a scheduled job

    try:
        ray_status = get_ray_status(job.ray_job_id)
    except Exception:
        log.exception(
            "Failed to query Ray status for job %s (%s)",
            job.job_id,
            job.ray_job_id,
        )
        return

    if ray_status in ("PENDING", "RUNNING"):
        log.info("Stopping Ray job %s (stop_requested on %s)", job.ray_job_id, key)
        stop_ray_job(job.ray_job_id)


def poll_once(
    executor: ProcessPoolExecutor,
    in_flight: dict[str, Future],
) -> None:
    """Single poll cycle: scan all jobs, dispatch work, handle stops."""
    for key in list(in_flight.keys()):
        if in_flight[key].done():
            del in_flight[key]
    experiments = list_experiments()
    log.info(
        "Poll: %d experiment(s), %d job(s) in flight",
        len(experiments),
        len(in_flight),
    )
    for experiment in experiments:
        jobs = list_experiment_run_jobs(experiment.run_id)
        log.info(
            "  %s/%s: %d job(s)",
            experiment.experiment_name,
            experiment.run_id,
            len(jobs),
        )
        for job in jobs:
            try:
                _dispatch_job(executor, in_flight, job)
            except Exception:
                log.exception("Failed to dispatch job %s", job.job_id)


def main() -> None:
    set_runs_on_server(True)
    executor = ProcessPoolExecutor(max_workers=STARTER_WORKERS)
    in_flight: dict[str, Future] = {}
    while True:
        try:
            poll_once(executor, in_flight)
            HEARTBEAT_PATH.touch()
        except Exception:
            log.exception("Error during poll cycle")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
