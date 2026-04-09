"""cortexflow — connect your code to the RoboLab compute cluster.

    import cortexflow

    cortexflow.init()

    with cortexflow.mlflow_run("my-experiment") as run:
        cortexflow.log_metric("loss", 0.5, step=1)
        cortexflow.save_checkpoint(model, optimizer, epoch=5)

    @cortexflow.remote(num_gpus=1)
    def train(config):
        ...

    cortexflow.get(train.remote({"lr": 1e-3}))
"""

from __future__ import annotations

import ray

from cortexflow.config import CortexConfig, get_config, set_config
from cortexflow.mlflow_util import (
    get_mlflow_client,
    load_checkpoint,
    log_artifact,
    log_metric,
    log_metrics,
    log_params,
    mlflow_run,
    save_checkpoint,
)
from cortexflow.ray_util import get, get_ray_client, remote
from cortexflow.s3_util import download, get_s3_client, upload


def init(ray_address: str | None = None) -> None:
    """Configure connections to Ray, MLflow, and S3.

    Reads configuration from environment variables:
        RAY_ADDRESS, MLFLOW_TRACKING_URI, DGX_TAILSCALE_IP, etc.

    Call once at the top of your script.
    """
    config = CortexConfig.from_env()
    if ray_address:
        config.ray_address = ray_address
    set_config(config)

    if config.ray_address and not ray.is_initialized():
        ray.init(address=config.ray_address, ignore_reinit_error=True)


__all__ = [
    "init",
    "remote",
    "get",
    "get_ray_client",
    "mlflow_run",
    "log_metric",
    "log_metrics",
    "log_params",
    "log_artifact",
    "save_checkpoint",
    "load_checkpoint",
    "get_mlflow_client",
    "upload",
    "download",
    "get_s3_client",
]
