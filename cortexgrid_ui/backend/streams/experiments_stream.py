"""Experiments + runs streams.

Two Refreshers fed by cortexgrid's experiment and run records:
  * `experiments_meta_refresher` (pinned, single topic): per-experiment
    metadata (name + creation time). Always warm so the UI's experiment
    list is available without per-experiment subscriptions.
  * `runs_refresher` (subscriber-driven, keyed by experiment_name): the
    runs and their job statuses for one experiment. Polled only while a
    listener is attached, dropped from the sweep on last unsubscribe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from cortexgrid import state
from cortexgrid.jobs import list_experiment_run_jobs
from cortexgrid.ray_util import (
    get_ray_job_id_for_cortexgrid_job,
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
)

from cortexgrid_ui.backend.streams.config import EXPERIMENTS_STREAM_POLL_INTERVAL_SEC
from cortexgrid_ui.backend.utils.keyed_stream import KeyedCache, Refresher

log = logging.getLogger(__name__)


ExperimentName = str
RunId = str
RunName = str
JobId = str
META_TOPIC: None = None


@dataclass
class JobStatus:
    job_id: JobId
    status: str


@dataclass
class ExperimentMeta:
    name: ExperimentName
    created_at_ms: int | None


@dataclass
class Run:
    experiment_name: ExperimentName
    run_id: RunId
    run_name: RunName
    jobs: list[JobStatus]
    started_at_ms: int | None = None
    ended_at_ms: int | None = None


def _ms(timestamp: str) -> int:
    """An ISO 8601 timestamp from the control plane, as ms since the epoch."""
    return int(datetime.fromisoformat(timestamp).timestamp() * 1000)


def _build_run(
    run_id: RunId,
    experiment_name: ExperimentName,
    run_name: RunName,
    job_ids: list[JobId],
    all_ray_submission_ids: list[str],
    started_at_ms: int,
) -> Run:
    jobs = []
    for job_id in job_ids:
        try:
            ray_job_id = get_ray_job_id_for_cortexgrid_job(
                run_id, job_id, all_ray_submission_ids
            )
            status = get_ray_job_status(ray_job_id).value
        except Exception:
            status = "broken"
        jobs.append(JobStatus(job_id=job_id, status=status))
    return Run(
        experiment_name=experiment_name,
        run_id=run_id,
        run_name=run_name,
        jobs=jobs,
        started_at_ms=started_at_ms,
        # Nothing ends a run: it lasts until it is deleted.
        ended_at_ms=None,
    )


def poll_experiments_meta(_: None) -> dict[ExperimentName, ExperimentMeta]:
    return {
        e["name"]: ExperimentMeta(name=e["name"], created_at_ms=_ms(e["created_at"]))
        for e in state.get("experiments")
    }


def poll_runs(experiment_name: ExperimentName) -> dict[RunName, Run]:
    records = state.get("runs", params={"experiment_name": experiment_name})
    if not records:
        return {}
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    runs: dict[RunName, Run] = {}
    for record in records:
        run_id = record["run_id"]
        try:
            job_ids = [job.job_id for job in list_experiment_run_jobs(run_id)]
            run = _build_run(
                run_id,
                experiment_name,
                record["run_name"],
                job_ids,
                all_ray_submission_ids,
                _ms(record["created_at"]),
            )
        except Exception:
            log.exception("Building run data failed for %s", run_id)
            continue
        runs[run.run_name] = run
    return runs


experiments_meta_cache: KeyedCache[None, ExperimentName, ExperimentMeta] = KeyedCache()
runs_cache: KeyedCache[ExperimentName, RunName, Run] = KeyedCache()

experiments_meta_refresher: Refresher[None, ExperimentName, ExperimentMeta] = Refresher(
    name="experiments_meta_stream",
    cache=experiments_meta_cache,
    poll_fn=poll_experiments_meta,
    poll_interval_sec=EXPERIMENTS_STREAM_POLL_INTERVAL_SEC,
)

runs_refresher: Refresher[ExperimentName, RunName, Run] = Refresher(
    name="runs_stream",
    cache=runs_cache,
    poll_fn=poll_runs,
    poll_interval_sec=EXPERIMENTS_STREAM_POLL_INTERVAL_SEC,
)


def runs_for_experiment(experiment_name: ExperimentName) -> list[RunName]:
    return list(runs_cache.get(experiment_name).keys())


def resolve_run_id(run_name: RunName) -> RunId:
    for runs in runs_cache._data.values():
        if run_name in runs:
            return runs[run_name].run_id
    raise KeyError(f"unknown run_name: {run_name}")


def resolve_run_name(run_id: RunId) -> RunName:
    for runs in runs_cache._data.values():
        for r in runs.values():
            if r.run_id == run_id:
                return r.run_name
    raise KeyError(f"unknown run_id: {run_id}")
