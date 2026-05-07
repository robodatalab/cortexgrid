from __future__ import annotations

import logging
import time
import unittest
from typing import Any, Callable, Literal, get_args
import uuid

import cortexflow


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(name)


def experiment_name(test: unittest.TestCase) -> str:
    return f"it-{test._testMethodName}-{uuid.uuid4().hex[:8]}"


def _schedule_and_wait(
    fn: Callable[..., None],
    *args: Any,
    retry: bool = False,
    **kwargs: Any,
) -> None:
    """Submit fn as a Ray job; wait until it FINISHES. With retry=True, intermediate FAILED is ignored. STOPPED is always terminal."""
    timeout = 600

    job_id = cortexflow.remote(fn, *args, retry=retry, **kwargs)
    run_id = cortexflow.Experiment.get_instance().run_id
    deadline = time.monotonic() + timeout
    printed: set[str] = set()
    while time.monotonic() < deadline:
        match = next(
            (
                job
                for job in cortexflow.list_experiment_run_jobs(run_id)
                if job.job_id == job_id
            ),
            None,
        )
        ray_job_id = match.get_ray_job_id() if match else None
        if ray_job_id:
            status = cortexflow.get_ray_job_status(ray_job_id)
            terminal = status in (
                cortexflow.JobStatus.FINISHED,
                cortexflow.JobStatus.FAILED,
                cortexflow.JobStatus.STOPPED,
            )
            if terminal and ray_job_id not in printed:
                print(
                    f"--- ray job {ray_job_id} logs ---\n{cortexflow.get_ray_logs(ray_job_id)}"
                )
                printed.add(ray_job_id)
            if status == cortexflow.JobStatus.FINISHED:
                return
            if status == cortexflow.JobStatus.STOPPED:
                raise AssertionError(f"job {job_id} ended in {status}")
            if status == cortexflow.JobStatus.FAILED and not retry:
                raise AssertionError(f"job {job_id} ended in {status}")
        time.sleep(5)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _run_local(
    log: logging.Logger,
    fn: Callable[..., None],
    *args: Any,
    retry: bool = False,
    **kwargs: Any,
) -> None:
    if retry:
        errors = []
        available_retries = 5
        finished = False
        while not finished and available_retries > 0:
            available_retries -= 1
            try:
                fn(*args, **kwargs)
                finished = True
            except Exception as ex:
                finished = False
                errors.append(ex)
                log.exception("Exception caught: %r", ex)

        if not finished:
            raise errors[-1]
    else:
        fn(*args, **kwargs)


RunMode = Literal["local", "remote"]
RUN_MODES = [(m,) for m in get_args(RunMode)]


def run(
    mode: RunMode,
    log: logging.Logger,
    fn: Callable[..., None],
    *args: Any,
    retry: bool = False,
    **kwargs: Any,
) -> None:
    if mode == "local":
        _run_local(log, fn, *args, retry=retry, **kwargs)
    elif mode == "remote":
        _schedule_and_wait(fn, *args, retry=retry, **kwargs)
