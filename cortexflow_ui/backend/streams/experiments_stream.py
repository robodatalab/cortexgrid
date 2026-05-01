"""Experiments stream.

Polls MLflow + Ray and emits per-run diff events. The poll runs from
FastAPI lifespan via ``stream.start(TOPIC)`` and stays alive regardless
of subscribers, so other modules (``notes.py``,
``experiment_notes_stream``) can read the cache directly via
``runs_for_experiment`` / ``resolve_run_id`` / ``resolve_run_name``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from cortexflow.experiment import list_experiments
from cortexflow.ray_util import (
    get_ray_job_id_for_cortexflow_job,
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
)
from cortexflow_ui.backend.streams.config import EXPERIMENTS_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.utils.keyed_stream import KeyedCache, KeyedStream

log = logging.getLogger(__name__)


ExperimentName = str
RunId = str
RunName = str
JobId = str
TOPIC: None = None


@dataclass
class JobStatus:
    job_id: JobId
    status: str


@dataclass
class Run:
    experiment_name: ExperimentName
    run_id: RunId
    run_name: RunName
    jobs: list[JobStatus]


def _build_run(exp, all_ray_submission_ids: list[str]) -> Run:
    jobs = []
    for job_id in exp.get_jobs():
        try:
            ray_job_id = get_ray_job_id_for_cortexflow_job(
                exp.run_id, job_id, all_ray_submission_ids
            )
            status = get_ray_job_status(ray_job_id).value
        except Exception:
            status = "broken"
        jobs.append(JobStatus(job_id=job_id, status=status))
    return Run(
        experiment_name=exp.experiment_name,
        run_id=exp.run_id,
        run_name=exp.run_name(),
        jobs=jobs,
    )


def poll_experiments(_: None) -> dict[RunName, Run]:
    experiments = list_experiments()
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    runs: dict[RunName, Run] = {}
    for exp in experiments:
        try:
            run = _build_run(exp, all_ray_submission_ids)
        except Exception:
            log.exception("Building run data failed for %s", exp.run_id)
            continue
        runs[run.run_name] = run
    return runs


cache: KeyedCache[None, RunName, Run] = KeyedCache()

stream: KeyedStream[None, RunName, Run] = KeyedStream(
    name="experiments_stream",
    cache=cache,
    poll_fn=poll_experiments,
    poll_interval_sec=EXPERIMENTS_STREAM_POLL_INTERVAL_SEC,
)


def runs_for_experiment(experiment_name: ExperimentName) -> list[RunName]:
    return [
        r.run_name
        for r in cache.get(TOPIC).values()
        if r.experiment_name == experiment_name
    ]


def resolve_run_id(run_name: RunName) -> RunId:
    runs = cache.get(TOPIC)
    if run_name in runs:
        return runs[run_name].run_id
    raise KeyError(f"unknown run_name: {run_name}")


def resolve_run_name(run_id: RunId) -> RunName:
    for r in cache.get(TOPIC).values():
        if r.run_id == run_id:
            return r.run_name
    raise KeyError(f"unknown run_id: {run_id}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stream.start(TOPIC)
    try:
        yield
    finally:
        pass
