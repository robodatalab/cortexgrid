"""Entry point: python -m jobs_control_plane <experiment_name> <run_id>"""

from __future__ import annotations

import logging
import sys

from cortexflow.experiment import Experiment
from jobs_control_plane.server import run


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    if len(sys.argv) != 3:
        print(f"Usage: python -m jobs_control_plane <experiment_name> <run_id>")
        sys.exit(1)

    experiment = Experiment.from_experiment(sys.argv[1], sys.argv[2])
    run(experiment)


if __name__ == "__main__":
    main()
