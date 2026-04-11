from __future__ import annotations

import logging

from cortexflow.config import CortexConfig, set_config, get_config
from cortexflow import mlflow_util


def init(experiment: str | None = None) -> None:
    """Configure connections to Ray, MLflow, and S3.

    Call once at the top of your script.

    Args:
        experiment: name of the experiment. If None, a new experiment will be created.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    
    config = get_config()
    if config is None:
        config = CortexConfig.from_secrets_manager()
        set_config(config)

        mlflow_util.try_create_experiment_and_run(experiment=experiment)
