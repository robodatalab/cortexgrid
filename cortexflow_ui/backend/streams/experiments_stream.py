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
from cortexflow_ui.backend.streams.config import EXPERIMENTS_STREAM_POLL_INTERVAL_SEC

log = logging.getLogger(__name__)

runs_cache: dict[str, dict] = {}
ws_clients: set[WebSocket] = set()
force_refresh = asyncio.Event()


def runs_for_experiment(experiment_name: str) -> list[str]:
    global runs_cache
    return [
        r["run_name"]
        for r in runs_cache.values()
        if r["experiment_name"] == experiment_name
    ]


def resolve_run_id(run_name: str) -> str:
    global runs_cache
    for r in runs_cache.values():
        if r["run_name"] == run_name:
            return r["run_id"]
    raise KeyError(f"unknown run_name: {run_name}")


def resolve_run_name(run_id: str) -> str:
    global runs_cache
    return runs_cache[run_id]["run_name"]


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


async def _broadcast(event: dict) -> None:
    for ws in list(ws_clients):
        try:
            await ws.send_json(event)
        except Exception:
            ws_clients.discard(ws)


async def _wait_for_next_poll() -> None:
    try:
        await asyncio.wait_for(
            force_refresh.wait(), timeout=EXPERIMENTS_STREAM_POLL_INTERVAL_SEC
        )
    except asyncio.TimeoutError:
        return
    force_refresh.clear()
    runs_cache.clear()
    await _broadcast({"type": "cleared"})


async def _poll_loop() -> None:
    log.info("Experiments poll loop started")
    while True:
        try:
            experiments = await asyncio.to_thread(list_experiments)
            all_ray_submission_ids = await asyncio.to_thread(
                list_ray_jobs_with_submission_id
            )
        except Exception:
            log.exception("Experiments poll failed")
            await _wait_for_next_poll()
            continue
        seen: set[str] = set()
        added_count = 0
        for exp in experiments:
            try:
                run = await asyncio.to_thread(
                    _build_run_data, exp, all_ray_submission_ids
                )
            except Exception:
                log.exception("Building run data failed for %s", exp.run_id)
                continue
            seen.add(run["run_id"])
            if run["run_id"] not in runs_cache:
                added_count += 1
                await _broadcast({"type": "added", "run": run})
            runs_cache[run["run_id"]] = run
        removed = [rid for rid in list(runs_cache) if rid not in seen]
        for run_id in removed:
            del runs_cache[run_id]
            await _broadcast({"type": "removed", "run_id": run_id})
        log.info(
            "Experiments poll: %d cached, %d added, %d removed, %d clients",
            len(runs_cache),
            added_count,
            len(removed),
            len(ws_clients),
        )
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
