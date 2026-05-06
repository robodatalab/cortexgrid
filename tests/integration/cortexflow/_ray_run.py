from __future__ import annotations

import time
from typing import Any, Callable

import cortexflow


def schedule_and_wait(
    fn: Callable[..., None],
    *args: Any,
    timeout: float = 600.0,
    retry: bool = False,
    **kwargs: Any,
) -> None:
    """Submit fn as a Ray job; wait until it FINISHES. With retry=True, intermediate FAILED is ignored. STOPPED is always terminal."""
    job_id = cortexflow.remote(fn, *args, retry=retry, **kwargs)
    run_id = cortexflow.Experiment.get_instance().run_id
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = next(
            (l for l in cortexflow.list_experiment_run_jobs(run_id) if l.job_id == job_id),
            None,
        )
        ray_job_id = match.get_ray_job_id() if match else None
        if ray_job_id:
            status = cortexflow.get_ray_job_status(ray_job_id)
            if status == cortexflow.JobStatus.FINISHED:
                return
            if status == cortexflow.JobStatus.STOPPED:
                raise AssertionError(f"job {job_id} ended in {status}")
            if status == cortexflow.JobStatus.FAILED and not retry:
                raise AssertionError(f"job {job_id} ended in {status}")
        time.sleep(5)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")
