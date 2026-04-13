"""Cluster-side entrypoint. Run by Ray as: python -m cortexflow._ray_job_driver payload.pkl"""

from __future__ import annotations

import cloudpickle  # type: ignore
import logging
import sys
from pathlib import Path

from cortexflow.checkpoint import set_cortexflow_job_id
from cortexflow.experiment import set_instance, set_runs_on_server
from cortexflow.jobs import Payload


def main(payload_path: str) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    payload: Payload = cloudpickle.loads(Path(payload_path).read_bytes())
    set_cortexflow_job_id(payload.job_id)
    set_instance(payload.experiment)
    set_runs_on_server(True)
    payload.fn(*payload.args, **payload.kwargs)


if __name__ == "__main__":
    main(sys.argv[1])
