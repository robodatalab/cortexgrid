"""Jobs control plane — polls MLflow for pending jobs and submits them to Ray.

Architecture:

- ``JobLifecycle`` is pure static identity plus the ``stop_requested`` and
  ``retry`` latches. Execution status is never persisted.
- Ray is the source of truth for execution state. Each poll cycle reconciles
  cortexflow jobs (from MLflow) against the set of Ray submissions returned
  by ``list_ray_jobs_with_submission_id``.
- Submission to Ray is async — handed to a ``ProcessPoolExecutor`` so a
  large payload upload cannot block the poll loop.
- Submission ids are shaped ``{run_id}-{job_id}-{attempt}``. Each retry
  uses a fresh attempt suffix so Ray never sees a duplicate id.
- A single heartbeat file is touched at the end of every successful
  poll cycle. The Docker healthcheck watches its mtime.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import Future, ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from cortexflow import (
    JobLifecycle,
    LifecycleEvent,
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
    """Submit a single job to Ray. Runs in a ProcessPoolExecutor subprocess.

    May raise: exceptions propagate to the Future and surface on the next
    poll cycle. The lifecycle is never mutated here — Ray is the source of
    truth, and the next poll observes whatever state Ray ended up in.
    """
    set_runs_on_server(True)
    lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)

    if lifecycle.stop_requested:
        log.info("Worker skipping job %s: stop_requested is set", job_id)
        return

    submission_id = ray_submission_id(run_id, job_id, attempt)

    payload = Payload.load_from_mlflow(run_id, job_id)
    payload_pkl_path = Path(payload.project_code_root) / "payload.pkl"
    requirements_txt_path = Path(payload.project_code_root) / "requirements.txt"
    submit_ray_job(
        submission_id=submission_id,
        entrypoint=f"python -m cortexflow._ray_job_driver {str(payload_pkl_path)}",
        runtime_env={
            "working_dir": payload.project_code_root,
            "pip": str(requirements_txt_path),
        },
        num_gpus=payload.num_gpus,
        num_cpus=payload.num_cpus,
    )


def _record_state(cjob: JobLifecycle, rjob: str | None) -> None:
    """Append a lifecycle event when the observed (attempt, state) changes.

    Ray owns the state machine; we only observe. On every poll we compare
    the latest observation against the last history entry and, on a
    mismatch, close the prior entry and append a new one. The new entry
    records the Ray submission id observed at the time (``None`` before
    the worker has successfully handed the job off to Ray).
    """
    attempt = get_ray_job_attempt(rjob)
    state = get_ray_job_status(rjob).value
    now = datetime.now(timezone.utc).isoformat()
    last = cjob.history[-1] if cjob.history else None
    if last is not None and last.attempt == attempt and last.state == state:
        return
    if last is not None:
        last.end = now
    cjob.history.append(
        LifecycleEvent(attempt=attempt, state=state, start=now, ray_job_id=rjob)
    )
    cjob.save_to_mlflow()


def _match_ray_jobs_to_cortexflow_jobs(
    cortexflow_jobs: list[JobLifecycle],
) -> list[tuple[JobLifecycle, str | None]]:
    all_ray_submission_ids = list_ray_jobs_with_submission_id()

    pairs: list[tuple[JobLifecycle, str | None]] = []
    for cjob in cortexflow_jobs:
        prefix = ray_submission_id(cjob.run_id, cjob.job_id, None) + "-"
        attempts = [sid for sid in all_ray_submission_ids if sid.startswith(prefix)]
        latest = max(attempts, key=get_ray_job_attempt) if attempts else None
        pairs.append((cjob, latest))
    return pairs


def poll_once(
    executor: ProcessPoolExecutor,
    in_flight: dict[str, tuple[str, str, Future]],
) -> None:
    """Single poll cycle: scan all jobs, dispatch work, handle stops."""
    for submission_id_core in list(in_flight.keys()):
        run_id, job_id, future = in_flight[submission_id_core]
        if not future.done():
            continue
        exc = future.exception()
        if exc is not None:
            log.error("Worker for %s failed: %s", submission_id_core, exc)
            try:
                lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)
                if lifecycle.history:
                    lifecycle.history[-1].error = str(exc)
                    lifecycle.save_to_mlflow()
            except Exception:
                log.exception("Failed to persist error for %s", submission_id_core)
        del in_flight[submission_id_core]

    experiments = list_experiments()
    cortexflow_jobs = [
        job
        for experiment in experiments
        for job in list_experiment_run_jobs(experiment.run_id)
    ]
    cortexflow_to_ray_jobs = _match_ray_jobs_to_cortexflow_jobs(cortexflow_jobs)

    for cjob, rjob in cortexflow_to_ray_jobs:
        _record_state(cjob, rjob)
        submission_id_core = ray_submission_id(cjob.run_id, cjob.job_id, None)
        if submission_id_core in in_flight:
            continue

        if cjob.stop_requested:
            if rjob is not None:
                stop_ray_job(rjob)
            continue

        if rjob is None:
            in_flight[submission_id_core] = (
                cjob.run_id,
                cjob.job_id,
                executor.submit(_submit_job_worker, cjob.run_id, cjob.job_id, 0),
            )
            continue

        if get_ray_job_status(rjob) == JobStatus.FAILED and cjob.retry:
            attempt = get_ray_job_attempt(rjob)
            in_flight[submission_id_core] = (
                cjob.run_id,
                cjob.job_id,
                executor.submit(
                    _submit_job_worker, cjob.run_id, cjob.job_id, attempt + 1
                ),
            )


def main() -> None:
    set_runs_on_server(True)
    executor = ProcessPoolExecutor(max_workers=STARTER_WORKERS)
    in_flight: dict[str, tuple[str, str, Future]] = {}
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
