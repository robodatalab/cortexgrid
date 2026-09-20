"""The one place a job's displayed status is decided.

Every view of a job — the experiments tree, a run's job list, the job
dashboard, the cluster-wide jobs table — shows the same word for the same
job, because they all ask here. A status one view can show and another
cannot is a bug the moment two views are open at once.

Ray owns execution state, so it is the default answer. The job's own
record overrides it where the record knows something Ray cannot: a job
whose delete_requested latch is set is on its way out whatever Ray still
says about its last attempt.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

from cortexgrid.jobs import (
    JobLifecycle,
    list_experiment_run_job_ids,
    load_job,
)
from cortexgrid.ray_util import JobStatus, get_ray_job_status, list_ray_job_statuses

log = logging.getLogger(__name__)

# A sweep reads one record per job, and each is a round trip to the
# tracking server. Measured against the cluster at 29 jobs: 4.9s with one
# reader, ~2.5s with four to sixteen. The server answers these more or
# less one at a time, so past a handful more readers buy nothing.
_READERS = 8

Item = TypeVar("Item")
Result = TypeVar("Result")

DELETING = "deleting"
BROKEN = "broken"


RayStatuses = dict[str, JobStatus]


def ray_snapshot() -> RayStatuses:
    """Every Ray job's status, read once for a whole sweep.

    A view with many jobs asks Ray once and looks each job up here.
    Asking per job is an HTTP round trip each, which is most of a sweep's
    wall clock once a cluster has a few dozen of them.
    """
    return list_ray_job_statuses()


def ray_status(ray_job_id: str | None, statuses: RayStatuses | None = None) -> str:
    """What Ray says, or `broken` when it cannot say anything.

    With a snapshot, a job Ray has no record of reads `broken`: it ran,
    and Ray has since forgotten it. Without one this asks Ray directly,
    for the single-job views where a snapshot would cost more than it
    saves. Either way a job Ray chokes on costs its own row a status,
    never the sweep it was part of.
    """
    if ray_job_id is None:
        return JobStatus.PENDING.value
    if statuses is not None:
        status = statuses.get(ray_job_id)
        return status.value if status is not None else BROKEN
    try:
        return get_ray_job_status(ray_job_id).value
    except Exception:
        log.exception("Ray status lookup failed for %s", ray_job_id)
        return BROKEN


def job_status(
    job: JobLifecycle,
    ray_job_id: str | None,
    statuses: RayStatuses | None = None,
) -> str:
    """The word every view shows for this job."""
    if job.delete_requested:
        return DELETING
    return ray_status(ray_job_id, statuses)


def in_parallel(
    fn: Callable[[Item], Result], items: Iterable[Item]
) -> list[Result]:
    """Map `fn` over `items` across threads, keeping the input order."""
    items = list(items)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(_READERS, len(items))) as pool:
        return list(pool.map(fn, items))


def _job_ids_of_run(run_id: str) -> list[str]:
    """The run's job ids, or none if the run cannot be listed right now.

    One unreadable run must not cost a sweep every other row it covers.
    """
    try:
        return list_experiment_run_job_ids(run_id)
    except Exception:
        log.exception("Listing jobs failed for run %s", run_id)
        return []


def jobs_of_runs(run_ids: Iterable[str]) -> dict[str, list[JobLifecycle]]:
    """Every job of each run.

    Both halves go wide, and the second is the one that matters: the
    listings are one call per run, while the records are one download per
    job, and jobs outnumber runs. Reading them run by run would leave the
    sweep waiting on HTTP for most of its wall clock.
    """
    run_ids = list(run_ids)
    ids_by_run = dict(zip(run_ids, in_parallel(_job_ids_of_run, run_ids)))
    pairs = [(run_id, job_id) for run_id, ids in ids_by_run.items() for job_id in ids]
    loaded = in_parallel(lambda pair: load_job(*pair), pairs)
    jobs: dict[str, list[JobLifecycle]] = {run_id: [] for run_id in run_ids}
    for (run_id, _job_id), job in zip(pairs, loaded):
        if job is not None:
            jobs[run_id].append(job)
    return jobs
