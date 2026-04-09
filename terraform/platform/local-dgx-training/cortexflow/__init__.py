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


def init() -> None:
    """Configure connections to Ray, MLflow, and S3.

    If running inside a Ray job (env vars already injected by cortexflow.remote),
    reads config from env and does not re-init Ray.

    On the Mac: pulls secrets from AWS Secrets Manager and connects to the Ray
    cluster via the Ray Client protocol (ray://<ip>:10001).

    Call once at the top of your script.
    """
    import os

    import ray

    from cortexflow.config import CortexConfig, set_config

    inside_ray_job = bool(os.environ.get("DGX_TAILSCALE_IP"))

    if inside_ray_job:
        config = CortexConfig.from_env()
    else:
        config = CortexConfig.from_secrets_manager()

    set_config(config)

    if not ray.is_initialized() and config.dgx_ip:
        ray.init(address=f"ray://{config.dgx_ip}:10001", ignore_reinit_error=True)


def __getattr__(name: str):  # noqa: ANN204
    """Lazy imports so heavy deps (ray, mlflow, boto3) aren't loaded at import time."""
    if name in ("remote", "get", "get_ray_client"):
        from cortexflow import ray_util
        return getattr(ray_util, name)

    if name in (
        "mlflow_run", "log_metric", "log_metrics", "log_params",
        "log_artifact", "save_checkpoint", "load_checkpoint", "get_mlflow_client",
    ):
        from cortexflow import mlflow_util
        return getattr(mlflow_util, name)

    if name in ("upload", "download", "get_s3_client"):
        from cortexflow import s3_util
        return getattr(s3_util, name)

    raise AttributeError(f"module 'cortexflow' has no attribute {name!r}")


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
