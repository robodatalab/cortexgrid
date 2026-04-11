"""Cluster-side entrypoint. Run by Ray as: python -m cortexflow._ray_job_driver payload.pkl"""

from __future__ import annotations

import cloudpickle  # type: ignore
import logging
import sys
from pathlib import Path

from cortexflow.config import set_config
from cortexflow.ray_util import Payload


def main(payload_path: str) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    payload: Payload = cloudpickle.loads(Path(payload_path).read_bytes())
    set_config(payload.config)
    payload.fn(*payload.args, **payload.kwargs)


if __name__ == "__main__":
    main(sys.argv[1])
