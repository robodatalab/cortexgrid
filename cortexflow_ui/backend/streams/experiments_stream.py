"""Experiments stream.

Polls MLflow + Ray and emits per-run diff events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cortexflow.experiment import list_experiments
from cortexflow.infra import get_mlflow_tracking_uri
from cortexflow.ray_util import (
    get_ray_job_id_for_cortexflow_job,
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
)
from mlflow.tracking import MlflowClient
from cortexflow_ui.backend.streams.config import EXPERIMENTS_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.utils.keyed_stream import KeyedCache, Refresher

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
    experiment_created_at_ms: int | None = None
    run_started_at_ms: int | None = None
    run_ended_at_ms: int | None = None


def _build_run(
    exp,
    all_ray_submission_ids: list[str],
    client: MlflowClient,
    exp_creation_times_ms: dict[str, int | None],
) -> Run:
    info = client.get_run(exp.run_id).info
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
        run_name=info.run_name or exp.run_id,
        jobs=jobs,
        experiment_created_at_ms=exp_creation_times_ms.get(exp.experiment_name),
        run_started_at_ms=info.start_time,
        run_ended_at_ms=info.end_time or None,
    )


def poll_experiments(_: None) -> dict[RunName, Run]:
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    exp_creation_times_ms = {
        e.name: e.creation_time for e in client.search_experiments()
    }
    experiments = list_experiments()
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    runs: dict[RunName, Run] = {}
    for exp in experiments:
        try:
            run = _build_run(
                exp, all_ray_submission_ids, client, exp_creation_times_ms
            )
        except Exception:
            log.exception("Building run data failed for %s", exp.run_id)
            continue
        runs[run.run_name] = run
    return runs


cache: KeyedCache[None, RunName, Run] = KeyedCache()

refresher: Refresher[None, RunName, Run] = Refresher(
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
