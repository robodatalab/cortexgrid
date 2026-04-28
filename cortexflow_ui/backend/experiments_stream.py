"""Experiments cache + WebSocket broadcast.

Owns the in-memory list of runs that the experiments-stream WebSocket
endpoint serves. A background poll loop refreshes the cache from
MLflow + Ray every ``POLL_INTERVAL_SEC`` seconds and emits diffs
(``added``/``removed``) to all connected clients. Setting the
``force_refresh`` event clears the cache and broadcasts ``cleared`` so
clients reset before the next poll repopulates them.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket

from cortexflow.experiment import list_experiments
from cortexflow.ray_util import (
    get_ray_job_id_for_cortexflow_job,
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
)

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 10

runs_cache: dict[str, dict] = {}
ws_clients: set[WebSocket] = set()
force_refresh = asyncio.Event()


def _build_run_data(exp, all_ray_submission_ids: list[str]) -> dict:
    jobs = []
    for job_id in exp.get_jobs():
        try:
            ray_job_id = get_ray_job_id_for_cortexflow_job(
                exp.run_id, job_id, all_ray_submission_ids
            )
            status = get_ray_job_status(ray_job_id).value
        except Exception:
            status = "broken"
        jobs.append({"job_id": job_id, "status": status})
    return {
        "experiment_name": exp.experiment_name,
        "run_id": exp.run_id,
        "run_name": exp.run_name(),
        "jobs": jobs,
    }


def _poll_once_blocking() -> list[dict]:
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    return [_build_run_data(exp, all_ray_submission_ids) for exp in list_experiments()]


async def _broadcast(event: dict) -> None:
    for ws in list(ws_clients):
        try:
            await ws.send_json(event)
        except Exception:
            ws_clients.discard(ws)


async def _wait_for_next_poll() -> None:
    try:
        await asyncio.wait_for(force_refresh.wait(), timeout=POLL_INTERVAL_SEC)
    except asyncio.TimeoutError:
        return
    force_refresh.clear()
    runs_cache.clear()
    await _broadcast({"type": "cleared"})


async def _poll_loop() -> None:
    log.info("Experiments poll loop started")
    while True:
        try:
            new_runs = await asyncio.to_thread(_poll_once_blocking)
        except Exception:
            log.exception("Experiments poll failed")
            await _wait_for_next_poll()
            continue
        new_by_id = {r["run_id"]: r for r in new_runs}
        added = [r for rid, r in new_by_id.items() if rid not in runs_cache]
        removed = [rid for rid in runs_cache if rid not in new_by_id]
        runs_cache.clear()
        runs_cache.update(new_by_id)
        log.info(
            "Experiments poll: %d cached, %d added, %d removed, %d clients",
            len(runs_cache), len(added), len(removed), len(ws_clients),
        )
        for run in added:
            await _broadcast({"type": "added", "run": run})
        for run_id in removed:
            await _broadcast({"type": "removed", "run_id": run_id})
        await _wait_for_next_poll()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_poll_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
