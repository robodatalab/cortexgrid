from __future__ import annotations

import logging
import os

from cortexflow.config import CortexConfig, set_config


def init(experiment: str | None = None) -> None:
    """Configure connections to Ray, MLflow, and S3.

    Call once at the top of your script.

    Args:
        experiment: name of the experiment. If None, a new experiment will be created.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    config = CortexConfig.from_secrets_manager()

    set_config(config)