"""Jobs control plane — polls MLflow for pending jobs and submits them to Ray.

Architecture:

- ``JobLifecycle`` is pure static identity plus the ``stop_requested``,
  ``delete_requested`` and ``retry`` latches. Execution status is never
  persisted.
- Callers never act on a job themselves: they write intent onto the
  job's own record and this loop is the only thing that submits, stops
  or deletes. A job created and deleted between two cycles is therefore
  seen once, as a record asking to be torn down, and is never submitted.
- Ray is the source of truth for execution state. Each poll cycle reconciles
  cortexgrid jobs (from MLflow) against the set of Ray submissions returned
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
from typing import Any

from cortexgrid import (
    JobLifecycle,
    LifecycleEvent,
    delete_prefix,
    delete_ray_job,
    delete_run_artifacts,
    experiment_has_active_runs,
    experiments_pending_deletion,
    finish_experiment_deletion,
    finish_run_deletion,
    get_ray_job_status,
    list_experiment_run_jobs,
    list_experiments,
    runs_pending_deletion,
    stop_ray_job,
    submit_ray_job,
    list_ray_jobs_with_submission_id,
    ray_submission_id,
    get_ray_job_attempt,
    JobStatus,
)


log = logging.getLogger("jobs-control-plane")

POLL_INTERVAL_SECONDS = int(os.environ.get("CORTEXGRID_POLL_INTERVAL", "5"))
STARTER_WORKERS = int(os.environ.get("CORTEXGRID_STARTER_WORKERS", "4"))
HEARTBEAT_PATH = Path("/tmp/cp_heartbeat")
TERMINAL_STATES = (JobStatus.FINISHED, JobStatus.FAILED, JobStatus.STOPPED)


def _submit_job_worker(run_id: str, job_id: str, attempt: int) -> None:
    """Submit a single job to Ray. Runs in a ProcessPoolExecutor subprocess.

    May raise: exceptions propagate to the Future and surface on the next
    poll cycle. The lifecycle is never mutated here — Ray is the source of
    truth, and the next poll observes whatever state Ray ended up in.
    """
    submission_id = ray_submission_id(run_id, job_id, attempt)

    try:
        log.info("Submitting a job (%s/%s) - loading lifecycle", run_id, job_id)
        lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)
        log.info("Submitting a job (%s/%s) - lifecycle loaded", run_id, job_id)

        if lifecycle.stop_requested or lifecycle.delete_requested:
            log.info(
                "Submitting a job (%s/%s) - Worker skipping job: %s is set",
                run_id,
                job_id,
                "delete_requested" if lifecycle.delete_requested else "stop_requested",
            )
            return

        log.info("Submitting a job (%s/%s) - downloading project code", run_id, job_id)
        project_code_root = lifecycle.download_project_code_root()
        log.info("Submitting a job (%s/%s) - project code downloaded", run_id, job_id)

        # working_dir carries the job's own source; Ray pip-installs the
        # third-party distributions the image lacks into a per-node cached
        # virtualenv layered on the image. No pip key when there are none, so
        # Ray builds no virtualenv.
        runtime_env: dict[str, Any] = {"working_dir": project_code_root}
        if lifecycle.pip_requirements:
            runtime_env["pip"] = lifecycle.pip_requirements
        log.info("Submitting a job (%s/%s) - submitting ray job", run_id, job_id)
        submit_ray_job(
            submission_id=submission_id,
            entrypoint="python -m cortexgrid._ray_job_driver payload.pkl",
            runtime_env=runtime_env,
            num_gpus=lifecycle.num_gpus,
            num_cpus=lifecycle.num_cpus,
        )
        log.info("Submitting a job (%s/%s) - ray job submitted", run_id, job_id)
    except Exception:
        submit_ray_job(
            submission_id=submission_id,
            entrypoint="exit 1",
            runtime_env={},
        )
        raise


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


def _match_ray_jobs_to_cortexgrid_jobs(
    cortexgrid_jobs: list[JobLifecycle],
) -> list[tuple[JobLifecycle, str | None]]:
    all_ray_submission_ids = list_ray_jobs_with_submission_id()

    pairs: list[tuple[JobLifecycle, str | None]] = []
    for cjob in cortexgrid_jobs:
        prefix = ray_submission_id(cjob.run_id, cjob.job_id, None) + "-"
        attempts = [sid for sid in all_ray_submission_ids if sid.startswith(prefix)]
        latest = max(attempts, key=get_ray_job_attempt) if attempts else None
        pairs.append((cjob, latest))
    return pairs


def _process_jobs_in_flight(
    in_flight: dict[str, tuple[str, str, Future]],
) -> dict[str, tuple[str, str, Future]]:
    updated_in_flight = {}
    for submission_id_core in in_flight.keys():
        run_id, job_id, future = in_flight[submission_id_core]

        if not future.done():
            updated_in_flight[submission_id_core] = (run_id, job_id, future)
            continue

        exc = future.exception()
        if exc is not None:
            log.error("poll_once - Worker for %s failed: %s", submission_id_core, exc)
            try:
                lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)
                if lifecycle.history:
                    lifecycle.history[-1].error = str(exc)
                    lifecycle.save_to_mlflow()
            except Exception:
                log.exception("Failed to persist error for %s", submission_id_core)

    return updated_in_flight


def _get_jobs_for_processing(
    in_flight: dict[str, tuple[str, str, Future]],
) -> list[tuple[JobLifecycle, str | None]]:
    # Records on their way out are hidden from everyone else; this loop is
    # what makes them go away, so it asks for them explicitly.
    experiments = list_experiments(include_deleting=True)
    cortexgrid_jobs = [
        job
        for experiment in experiments
        for job in list_experiment_run_jobs(experiment.run_id)
    ]
    all_cortexgrid_to_ray_jobs = _match_ray_jobs_to_cortexgrid_jobs(cortexgrid_jobs)

    # filter out the jobs that are still in flight
    not_in_flight_cortexgrid_to_ray_jobs = []
    for cjob, rjob in all_cortexgrid_to_ray_jobs:
        submission_id_core = ray_submission_id(cjob.run_id, cjob.job_id, None)
        if submission_id_core not in in_flight:
            not_in_flight_cortexgrid_to_ray_jobs.append((cjob, rjob))

    return not_in_flight_cortexgrid_to_ray_jobs


def _attempts_of(cjob: JobLifecycle, all_ray_submission_ids: list[str]) -> list[str]:
    """Every Ray submission this job has produced, across all attempts."""
    prefix = ray_submission_id(cjob.run_id, cjob.job_id, None) + "-"
    return [sid for sid in all_ray_submission_ids if sid.startswith(prefix)]


def _tear_down(cjob: JobLifecycle, all_ray_submission_ids: list[str]) -> None:
    """Advance a job that asked to be deleted by one cycle's worth of work.

    An attempt still in flight is stopped and the rest waits for the next
    cycle: Ray only releases a job that has settled, and a poll cycle must
    never block on one. Once every attempt is terminal the job's traces go
    in one pass — Ray's records, then the code package in S3, then the
    MLflow artifacts, whose lifecycle.json is the last thing that says this
    job ever existed.
    """
    attempts = _attempts_of(cjob, all_ray_submission_ids)
    live = [
        sid for sid in attempts if get_ray_job_status(sid) not in TERMINAL_STATES
    ]
    for sid in live:
        log.info("Tearing down %s/%s - stopping %s", cjob.run_id, cjob.job_id, sid)
        stop_ray_job(sid)
    if live:
        return
    for sid in attempts:
        delete_ray_job(sid)
    delete_prefix(f"job/{cjob.job_id}/")
    delete_run_artifacts(cjob.run_id, f"job/{cjob.job_id}")
    log.info("Tearing down %s/%s - done", cjob.run_id, cjob.job_id)


def _reap_records() -> None:
    """Remove the records whose contents have finished being torn down.

    Bottom-up, one level per cycle at most: a run goes once no job of it
    is left, and the experiment that held it goes once no run is left.
    """
    for run_id in runs_pending_deletion():
        if list_experiment_run_jobs(run_id):
            continue
        finish_run_deletion(run_id)
    for experiment_id in experiments_pending_deletion():
        if experiment_has_active_runs(experiment_id):
            continue
        finish_experiment_deletion(experiment_id)


def poll_once(
    executor: ProcessPoolExecutor,
    in_flight: dict[str, tuple[str, str, Future]],
) -> dict[str, tuple[str, str, Future]]:
    """Single poll cycle: scan all jobs, dispatch work, handle stops."""
    log.info("Poll once - starts")

    in_flight = _process_jobs_in_flight(in_flight)
    cortexgrid_to_ray_jobs = _get_jobs_for_processing(in_flight)
    log.info("Poll once - discovered %d cjob/rjob pairs", len(cortexgrid_to_ray_jobs))

    # Listed once per cycle, and only when something is being deleted:
    # teardown needs every attempt of a job, not just its latest.
    all_ray_submission_ids: list[str] | None = None

    for pair_idx, (cjob, rjob) in enumerate(cortexgrid_to_ray_jobs):
        if cjob.delete_requested:
            log.info(
                "Poll once(pair_idx=%d) - deleting job: cjob=%s rjob=%s",
                pair_idx,
                cjob,
                rjob,
            )
            if all_ray_submission_ids is None:
                all_ray_submission_ids = list_ray_jobs_with_submission_id()
            _tear_down(cjob, all_ray_submission_ids)
            continue

        _record_state(cjob, rjob)

        if cjob.stop_requested:
            if rjob is not None:
                log.info(
                    "Poll once(pair_idx=%d) - stopping job: cjob=%s rjob=%s",
                    pair_idx,
                    cjob,
                    rjob,
                )
                stop_ray_job(rjob)
            continue

        rjob_status = get_ray_job_status(rjob)
        ray_reported_events = [e for e in cjob.history if e.ray_job_id]
        if rjob is None and ray_reported_events:
            # Ray's store did not survive a head restart, so the last state it
            # reported stands; one still in flight died with the store.
            last = JobStatus(ray_reported_events[-1].state)
            in_flight_when_lost = last in (JobStatus.PENDING, JobStatus.RUNNING)
            cjob_status = JobStatus.FAILED if in_flight_when_lost else last
        else:
            cjob_status = rjob_status
        
        if cjob_status == JobStatus.PENDING:
            log.info(
                "Poll once(pair_idx=%d) - starting job: cjob=%s rjob=%s",
                pair_idx,
                cjob,
                rjob,
            )
            submission_id_core = ray_submission_id(cjob.run_id, cjob.job_id, None)
            in_flight[submission_id_core] = (
                cjob.run_id,
                cjob.job_id,
                executor.submit(_submit_job_worker, cjob.run_id, cjob.job_id, 0),
            )
        elif cjob_status == JobStatus.FAILED and cjob.retry:
            log.info(
                "Poll once(pair_idx=%d) - restarting job: cjob=%s rjob=%s",
                pair_idx,
                cjob,
                rjob,
            )
            attempt = get_ray_job_attempt(rjob)
            submission_id_core = ray_submission_id(cjob.run_id, cjob.job_id, None)
            in_flight[submission_id_core] = (
                cjob.run_id,
                cjob.job_id,
                executor.submit(
                    _submit_job_worker, cjob.run_id, cjob.job_id, attempt + 1
                ),
            )

    _reap_records()

    log.info("Poll once - ends")
    return in_flight


def main() -> None:
    executor = ProcessPoolExecutor(max_workers=STARTER_WORKERS)
    in_flight: dict[str, tuple[str, str, Future]] = {}
    while True:
        try:
            in_flight = poll_once(executor, in_flight)
            HEARTBEAT_PATH.touch()
        except Exception:
            log.exception("Error during poll cycle")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
