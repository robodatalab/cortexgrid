"""Per-job detail stream.

Polls job readiness + lifecycle history + Ray status every
``JOB_STREAM_POLL_INTERVAL_SEC`` seconds while at least one WebSocket
subscriber is watching the (run_id, job_id) pair. Pushes the full
detail dict on every poll.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from cortexgrid import s3_util, state
from cortexgrid.jobs import JobLifecycle
from cortexgrid.ray_util import get_ray_job_status, get_ray_job_url
from cortexgrid_ui.backend.streams.config import JOB_STREAM_POLL_INTERVAL_SEC
from cortexgrid_ui.backend.utils.keyed_stream import KeyedCache, Refresher

RunId = str
JobId = str
JobStreamKey = tuple[RunId, JobId]


@dataclass
class Readiness:
    code: bool
    lifecycle: bool
    lifecycle_error: str | None


@dataclass
class HistoryEntry:
    attempt: int
    state: str
    start: str
    end: str | None
    ray_job_id: str | None
    error: str | None
    ray_url: str | None


@dataclass
class JobDetail:
    job_id: JobId
    readiness: Readiness
    status: str | None = None
    retry: bool | None = None
    stop_requested: bool | None = None
    history: list[HistoryEntry] | None = None


def tarball_exists(run_id: str, job_id: str) -> bool:
    manifest = state.get("runs", run_id, "jobs", job_id, "manifest")
    if manifest is None:
        return False
    bucket, _, key = manifest["code_tarball_uri"].removeprefix("s3://").partition("/")
    try:
        s3_util.get_s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def poll_job(key: JobStreamKey) -> dict[JobId, JobDetail]:
    run_id, job_id = key
    try:
        lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)
    except FileNotFoundError:
        lifecycle = None
    readiness = Readiness(
        code=tarball_exists(run_id, job_id),
        lifecycle=lifecycle is not None,
        lifecycle_error=None if lifecycle is not None else "lifecycle not recorded",
    )
    if lifecycle is None:
        return {job_id: JobDetail(job_id=job_id, readiness=readiness)}

    ray_job_id = lifecycle.get_ray_job_id()
    history = [
        HistoryEntry(**asdict(event), ray_url=get_ray_job_url(event.ray_job_id))
        for event in lifecycle.history
    ]
    return {
        lifecycle.job_id: JobDetail(
            job_id=lifecycle.job_id,
            readiness=readiness,
            status=get_ray_job_status(ray_job_id).value,
            retry=lifecycle.retry,
            stop_requested=lifecycle.stop_requested,
            history=history,
        )
    }


cache: KeyedCache[JobStreamKey, JobId, JobDetail] = KeyedCache()

refresher: Refresher[JobStreamKey, JobId, JobDetail] = Refresher(
    name="job_details_stream",
    cache=cache,
    poll_fn=poll_job,
    poll_interval_sec=JOB_STREAM_POLL_INTERVAL_SEC,
)
