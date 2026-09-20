"""Cluster-wide jobs stream.

One topic, subscriber-driven: swept only while the Jobs tab is open and
dropped from the sweep again on unsubscribe.

A row exists here exactly when a job's lifecycle record does. The rows
come from ``list_experiments()`` for the (experiment, run) pairs and
``list_experiment_run_jobs(run_id)`` for the jobs each run owns, which is
the reader of the ``job/<job_id>/lifecycle.json`` artifacts. Ray is asked
once per sweep for its submission ids, and only to give a row that
already exists its status; it never contributes a row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cortexgrid.experiment import list_experiments
from cortexgrid.jobs import list_experiment_run_jobs
from cortexgrid.ray_util import (
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
)

from cortexgrid_ui.backend.streams.config import JOBS_STREAM_POLL_INTERVAL_SEC
from cortexgrid_ui.backend.utils.keyed_stream import KeyedCache, Refresher

log = logging.getLogger(__name__)


ExperimentName = str
RunId = str
JobId = str
RowId = str
JOBS_TOPIC: None = None


@dataclass
class JobRow:
    id: RowId
    job_id: JobId
    status: str
    experiment_name: ExperimentName
    run_id: RunId
    run_name: str


def row_id(run_id: RunId, job_id: JobId) -> RowId:
    """Job ids are unique within a run, so the pair identifies a row."""
    return f"{run_id}/{job_id}"


def _status(job, all_ray_submission_ids: list[str]) -> str:
    """The job's status as the rest of the UI words it.

    ``broken`` is what the experiments tree already shows for a job Ray
    cannot speak for, so the Jobs table says the same thing."""
    try:
        return get_ray_job_status(job.get_ray_job_id(all_ray_submission_ids)).value
    except Exception:
        return "broken"


def poll_jobs(_: None) -> dict[RowId, JobRow]:
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    rows: dict[RowId, JobRow] = {}
    for experiment in list_experiments():
        run_id = experiment.run_id
        try:
            jobs = list_experiment_run_jobs(run_id)
            if not jobs:
                continue
            run_name = experiment.run_name()
        except Exception:
            log.exception("Listing jobs failed for run %s", run_id)
            continue
        for job in jobs:
            rows[row_id(run_id, job.job_id)] = JobRow(
                id=row_id(run_id, job.job_id),
                job_id=job.job_id,
                status=_status(job, all_ray_submission_ids),
                experiment_name=experiment.experiment_name,
                run_id=run_id,
                run_name=run_name,
            )
    return rows


cache: KeyedCache[None, RowId, JobRow] = KeyedCache()

refresher: Refresher[None, RowId, JobRow] = Refresher(
    name="jobs_stream",
    cache=cache,
    poll_fn=poll_jobs,
    poll_interval_sec=JOBS_STREAM_POLL_INTERVAL_SEC,
)
