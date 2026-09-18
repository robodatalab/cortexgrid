"""Cluster-side entrypoint. Run by Ray as: python -m cortexgrid._ray_job_driver payload.pkl"""

from __future__ import annotations

import cloudpickle  # type: ignore
import logging
import sys
from pathlib import Path

from cortexgrid.checkpoint import set_cortexgrid_job_id
from cortexgrid.experiment import Experiment
from cortexgrid.jobs import JobResult, Payload

log = logging.getLogger("ray-job-driver")


def record(result: JobResult) -> None:
    """Persist the job's outcome beside its payload.

    Best effort: a job that ran must not be reported as failed because its
    result could not be uploaded. A caller waiting on the result gets
    JobResultUnavailable instead, and the reason is in this log."""
    try:
        result.save_to_mlflow()
    except Exception:
        log.exception("Failed to record result for job %s", result.job_id)


def main(payload_path: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("Loading payload: %s", payload_path)

    if not Path(payload_path).exists():
        raise FileNotFoundError(f"Payload not found: {payload_path}")

    payload: Payload = cloudpickle.loads(Path(payload_path).read_bytes())
    log.info("Payload loaded: %s", payload_path)
    log.info("Loading experiment: %s/%s", payload.experiment_name, payload.run_id)

    set_cortexgrid_job_id(payload.job_id)
    Experiment.from_experiment(payload.experiment_name, payload.run_id)
    log.info("Experiment loaded: %s/%s", payload.experiment_name, payload.run_id)

    log.info(
        "Starting job in experiment: %s/%s; args: %r; kwargs: %r",
        payload.experiment_name,
        payload.run_id,
        payload.args,
        payload.kwargs,
    )
    try:
        value = payload.fn(*payload.args, **payload.kwargs)
    except BaseException as exc:
        record(JobResult.from_exception(payload, exc))
        raise
    record(JobResult.from_value(payload, value))


if __name__ == "__main__":
    main(sys.argv[1])
