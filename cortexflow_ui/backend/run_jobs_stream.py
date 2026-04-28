"""Per-run jobs stream.

Polls ``list_experiment_run_jobs(run_id)`` + Ray status for each job
every ``RUN_JOBS_STREAM_POLL_INTERVAL_SEC`` seconds while at least one
WebSocket subscriber is watching that run. Pushes the full jobs array
on every poll.
"""
from __future__ import annotations

from cortexflow.jobs import list_experiment_run_jobs
from cortexflow.ray_util import (
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
)
from cortexflow_ui.backend.config import RUN_JOBS_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.keyed_stream import KeyedStream


def list_run_jobs(run_id: str) -> list[dict]:
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    return [
        {
            "job_id": j.job_id,
            "status": get_ray_job_status(
                j.get_ray_job_id(all_ray_submission_ids)
            ).value,
            "retry": j.retry,
        }
        for j in list_experiment_run_jobs(run_id)
    ]


stream = KeyedStream(
    name="run_jobs_stream",
    poll_fn=list_run_jobs,
    poll_interval_sec=RUN_JOBS_STREAM_POLL_INTERVAL_SEC,
)
