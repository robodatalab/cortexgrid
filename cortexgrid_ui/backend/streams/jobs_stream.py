"""Cluster-wide jobs stream.

One row per job the cluster knows about, from two sources reconciled on
every poll:

  * every job of every run of every experiment, read through cortexgrid's
    gate, so a run or experiment on its way out takes its jobs off this
    table the moment it is asked to go;
  * every Ray submission no such job claims — abandoned by a run torn
    down before this repo owned deletion, or left behind by a job whose
    record went without Ray being told. They have no owner to show and
    no lifecycle to inspect, and are here to be seen.

Status comes from Ray, except where the job's own record overrides it: a
job with the delete_requested latch set reads `deleting` until the
control plane has finished taking it apart.

Subscriber-driven and keyed on a single topic: polled only while the Jobs
view is open, dropped from the sweep on last unsubscribe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cortexgrid.experiment import search_experiments, search_runs
from cortexgrid.ray_util import ray_submission_id

from cortexgrid_ui.backend.streams.config import JOBS_STREAM_POLL_INTERVAL_SEC
from cortexgrid_ui.backend.streams.job_status import (
    RayStatuses,
    job_status,
    jobs_of_runs,
    ray_snapshot,
    ray_status,
)
from cortexgrid_ui.backend.utils.keyed_stream import KeyedCache, Refresher

log = logging.getLogger(__name__)


RowId = str
META_TOPIC: None = None


@dataclass
class JobRow:
    """A job as the Jobs table shows it.

    An `abandoned` row carries the run and job id parsed out of the Ray
    submission id, but no experiment or run name: whatever owned it is
    gone.
    """

    id: RowId
    job_id: str
    status: str
    experiment_name: str | None
    run_id: str | None
    run_name: str | None
    ray_job_id: str | None
    abandoned: bool


def row_id(run_id: str, job_id: str) -> RowId:
    return f"{run_id}/{job_id}"


def abandoned_row_id(run_id: str, job_id: str) -> RowId:
    return f"ray/{run_id}/{job_id}"


def _split_submission_id(ray_job_id: str) -> tuple[str, str] | None:
    """Recover (run_id, job_id) from `<run_id>-<job_id>-<attempt>`.

    Run ids never contain a dash and the attempt is always the last
    segment, so the job id is whatever sits between them. Ray's store also
    holds submissions this cluster did not name; they fit no part of this
    shape, and `None` says so.
    """
    run_id, _, rest = ray_job_id.partition("-")
    job_id, _, attempt = rest.rpartition("-")
    if not run_id or not job_id or not attempt.isdigit():
        return None
    return run_id, job_id


def _attempt_number(ray_job_id: str) -> int:
    return int(ray_job_id.rpartition("-")[2])


def _attempts_of(run_id: str, job_id: str, all_submission_ids: list[str]) -> list[str]:
    """The submissions named `<run_id>-<job_id>-<attempt>` for this job."""
    prefix = ray_submission_id(run_id, job_id, None) + "-"
    return [
        sid
        for sid in all_submission_ids
        if sid.startswith(prefix) and sid[len(prefix) :].isdigit()
    ]


def _owned_rows(
    all_submission_ids: list[str], statuses: RayStatuses
) -> tuple[dict[RowId, JobRow], set[str]]:
    """Every job under every run, and the Ray submissions they claim."""
    rows: dict[RowId, JobRow] = {}
    claimed: set[str] = set()
    runs_by_id = {
        run.info.run_id: (experiment, run)
        for experiment in search_experiments()
        for run in search_runs([experiment.experiment_id])
    }
    jobs_by_run = jobs_of_runs(runs_by_id)
    for run_id, (experiment, mlflow_run) in runs_by_id.items():
        for job in jobs_by_run[run_id]:
            attempts = _attempts_of(run_id, job.job_id, all_submission_ids)
            claimed.update(attempts)
            ray_job_id = max(attempts, key=_attempt_number, default=None)
            rows[row_id(run_id, job.job_id)] = JobRow(
                id=row_id(run_id, job.job_id),
                job_id=job.job_id,
                status=job_status(job, ray_job_id, statuses),
                experiment_name=experiment.name,
                run_id=run_id,
                run_name=mlflow_run.info.run_name or run_id,
                ray_job_id=ray_job_id,
                abandoned=False,
            )
    return rows, claimed


def _abandoned_rows(
    all_submission_ids: list[str], claimed: set[str], statuses: RayStatuses
) -> dict[RowId, JobRow]:
    """The unclaimed submissions, one row per job rather than per attempt."""
    attempts_by_job: dict[tuple[str, str], list[str]] = {}
    for ray_job_id in all_submission_ids:
        if ray_job_id in claimed:
            continue
        owner = _split_submission_id(ray_job_id)
        if owner is None:
            continue
        attempts_by_job.setdefault(owner, []).append(ray_job_id)
    rows: dict[RowId, JobRow] = {}
    for (run_id, job_id), attempts in attempts_by_job.items():
        latest = max(attempts, key=_attempt_number)
        rows[abandoned_row_id(run_id, job_id)] = JobRow(
            id=abandoned_row_id(run_id, job_id),
            job_id=job_id,
            status=ray_status(latest, statuses),
            experiment_name=None,
            run_id=run_id,
            run_name=None,
            ray_job_id=latest,
            abandoned=True,
        )
    return rows


def poll_jobs(_: None) -> dict[RowId, JobRow]:
    statuses = ray_snapshot()
    all_submission_ids = list(statuses)
    rows, claimed = _owned_rows(all_submission_ids, statuses)
    return {**rows, **_abandoned_rows(all_submission_ids, claimed, statuses)}


cache: KeyedCache[None, RowId, JobRow] = KeyedCache()

refresher: Refresher[None, RowId, JobRow] = Refresher(
    name="jobs_stream",
    cache=cache,
    poll_fn=poll_jobs,
    poll_interval_sec=JOBS_STREAM_POLL_INTERVAL_SEC,
)
