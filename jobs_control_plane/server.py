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
    get_ray_job_status,
    list_experiment_run_jobs,
    list_experiments,
    set_runs_on_server,
    stop_ray_job,
    submit_ray_job,
    list_ray_jobs_with_submission_id,
    ray_submission_id,
    get_ray_job_attempt,
    JobStatus,
)


log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("CORTEXFLOW_POLL_INTERVAL", "5"))
STARTER_WORKERS = int(os.environ.get("CORTEXFLOW_STARTER_WORKERS", "4"))
HEARTBEAT_PATH = Path("/tmp/cp_heartbeat")


def _submit_job_worker(run_id: str, job_id: str, attempt: int) -> None:
    """Async submission body run inside a ProcessPoolExecutor subprocess.

    Never raises. On any failure, logs and returns without mutating the
    lifecycle; the next poll cycle sees ray_job_id is still None and
    re-dispatches. Because the submission_id is deterministic, Ray will
    reject a duplicate submission on the retry and we proceed to save
    ray_job_id on the lifecycle.
    """
    set_runs_on_server(True)
    lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)

    if lifecycle.stop_requested:
        log.info("Worker skipping job %s: stop_requested is set", job_id)
        return

    submission_id = ray_submission_id(run_id, job_id, attempt)

    payload = Payload.load_from_mlflow(run_id, job_id)
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


def _match_ray_jobs_to_cortexflow_jobs(
    cortexflow_jobs: list[JobLifecycle],
) -> list[tuple[JobLifecycle, str | None]]:
    ray_jobs = list_ray_jobs_with_submission_id()

    # we will find all jobs (and all their attempts related to a specific job)
    matches: dict[str, list[str]] = {}
    submission_id_core_to_cortexflow_job: dict[str, JobLifecycle] = {}
    for cjob in cortexflow_jobs:
        submission_id_core = ray_submission_id(cjob.run_id, cjob.job_id, None)
        submission_id_core_to_cortexflow_job[submission_id_core] = cjob
        matches[submission_id_core] = []
        for submission_id in ray_jobs:
            if submission_id_core in submission_id:
                matches[submission_id_core].append(submission_id)

    # filter the list of submission ids for the one with the largest attempt id, and only leave that one
    pairs: list[tuple[JobLifecycle, str | None]] = []
    for submission_id_core, ray_jobs in matches.items():
        cjob = submission_id_core_to_cortexflow_job[submission_id_core]
        if ray_jobs:
            last_attempt_ray_job = list(
                sorted(ray_jobs, key=lambda k: get_ray_job_attempt(k))
            )[-1]
            pairs.append((cjob, last_attempt_ray_job))
        else:
            pairs.append((cjob, None))
    return pairs


def poll_once(
    executor: ProcessPoolExecutor,
    in_flight: dict[str, Future],
) -> None:
    """Single poll cycle: scan all jobs, dispatch work, handle stops."""
    # remove the finished _submit_job_worker futures from the list
    for submission_id_core in list(in_flight.keys()):
        if in_flight[submission_id_core].done():
            del in_flight[submission_id_core]

    experiments = list_experiments()
    cortexflow_jobs = [
        job
        for experiment in experiments
        for job in list_experiment_run_jobs(experiment.run_id)
    ]
    cortexflow_to_ray_jobs = _match_ray_jobs_to_cortexflow_jobs(cortexflow_jobs)

    for cjob, rjob in cortexflow_to_ray_jobs:
        assert cjob is not None

        submission_id_core = ray_submission_id(cjob.run_id, cjob.job_id, None)
        if submission_id_core in in_flight:
            # this entry is currently being processed by a worker process _submit_job_worker
            continue

        job_status = get_ray_job_status(rjob)
        attempt = get_ray_job_attempt(rjob)

        if job_status == JobStatus.PENDING:
            assert attempt == 0
            in_flight[submission_id_core] = executor.submit(
                _submit_job_worker, cjob.run_id, cjob.job_id, attempt
            )
        elif job_status == JobStatus.RUNNING:
            pass
        elif job_status == JobStatus.FINISHED:
            pass
        elif job_status == JobStatus.FAILED:
            if cjob.retry:
                in_flight[submission_id_core] = executor.submit(
                    _submit_job_worker,
                    cjob.run_id,
                    cjob.job_id,
                    attempt + 1,
                )
        elif job_status == JobStatus.STOPPED:
            pass

        if cjob.stop_requested and rjob is not None:
            stop_ray_job(rjob)


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
