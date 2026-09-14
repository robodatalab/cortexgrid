"""Per-run jobs stream.

Polls ``list_experiment_run_jobs(run_id)`` + Ray status for each job
every ``RUN_JOBS_STREAM_POLL_INTERVAL_SEC`` seconds while at least one
WebSocket subscriber is watching that run. Pushes the full jobs array
on every poll.
"""

from __future__ import annotations

from dataclasses import dataclass

from cortexgrid.jobs import list_experiment_run_jobs
from cortexgrid.ray_util import (
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
)
from cortexgrid_ui.backend.streams.config import RUN_JOBS_STREAM_POLL_INTERVAL_SEC
from cortexgrid_ui.backend.utils.keyed_stream import KeyedCache, Refresher

RunId = str
JobId = str


@dataclass
class Job:
    job_id: JobId
    status: str
    retry: bool


def list_run_jobs(run_id: RunId) -> dict[JobId, Job]:
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    return {
        j.job_id: Job(
            job_id=j.job_id,
            status=get_ray_job_status(j.get_ray_job_id(all_ray_submission_ids)).value,
            retry=j.retry,
        )
        for j in list_experiment_run_jobs(run_id)
    }


cache: KeyedCache[RunId, JobId, Job] = KeyedCache()

refresher: Refresher[RunId, JobId, Job] = Refresher(
    name="run_jobs_stream",
    cache=cache,
    poll_fn=list_run_jobs,
    poll_interval_sec=RUN_JOBS_STREAM_POLL_INTERVAL_SEC,
)
