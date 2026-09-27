import json
import tempfile
from pathlib import Path

import cortexgrid

from models import (
    FAMILY,
    LINEAR_SUFFIX,
    SQUARE_ROOTED_SUFFIX,
    SquareRooted,
)

XS = [0.0, 1.0, 2.0, 3.0]

def main() -> None:
    cortexgrid.Experiment.init("Examples-ModelDependencies")
    cortexgrid.register_model(
        SquareRooted,
        family=FAMILY,
        suffix=SQUARE_ROOTED_SUFFIX,
        requirements=SquareRooted.requirements(),
    )
    square_rooted = cortexgrid.deploy_model(
        FAMILY, SQUARE_ROOTED_SUFFIX, cortexgrid.IMPORTED, wait=True
    )
    try:
        print(f"square rooted: {SquareRooted.client(square_rooted.url).predict(XS)}")
    finally:
        cortexgrid.undeploy_model(square_rooted.key)
        cortexgrid.delete_model(FAMILY, SQUARE_ROOTED_SUFFIX, cortexgrid.IMPORTED)


if __name__ == "__main__":
    main()
