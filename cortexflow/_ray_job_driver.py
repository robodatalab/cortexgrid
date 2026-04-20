"""Cluster-side entrypoint. Run by Ray as: python -m cortexflow._ray_job_driver payload.pkl"""

from __future__ import annotations

import cloudpickle  # type: ignore
import logging
import sys
from pathlib import Path

from cortexflow.checkpoint import set_cortexflow_job_id
from cortexflow.experiment import Experiment
from cortexflow.jobs import Payload

log = logging.getLogger("ray-job-driver")


def main(payload_path: str) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    log.info("Loading payload: %s", payload_path)

    if not Path(payload_path).exists():
        raise FileNotFoundError(f"Payload not found: {payload_path}")

    payload: Payload = cloudpickle.loads(Path(payload_path).read_bytes())
    log.info("Payload loaded: %s", payload_path)
    log.info("Loading experiment: %s/%s", payload.experiment_name, payload.run_id)

    set_cortexflow_job_id(payload.job_id)
    Experiment.from_experiment(payload.experiment_name, payload.run_id)
    log.info("Experiment loaded: %s/%s", payload.experiment_name, payload.run_id)

    log.info(
        "Starting job in experiment: %s/%s; args: %r; kwargs: %r",
        payload.experiment_name,
        payload.run_id,
        payload.args,
        payload.kwargs,
    )
    payload.fn(*payload.args, **payload.kwargs)


if __name__ == "__main__":
    main(sys.argv[1])
