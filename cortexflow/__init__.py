"""cortexflow — connect your code to the RoboLab compute cluster.

import cortexflow

cortexflow.init()

# Fire-and-forget training on the DGX
train_fn = cortexflow.remote(num_gpus=1, max_retries=3)(my_train)
job = train_fn.remote(config)
print(f"Submitted: {job.job_id}")

# Check on it later
info = cortexflow.status(job.job_id)

# Inside the training function — checkpoint after each epoch
with cortexflow.checkpoint() as ckpt:
    ckpt.epoch = epoch
    ckpt.save_training_state(model, optimizer, scheduler)

# On resume — load checkpoint if it exists
ckpt = cortexflow.resume()
if ckpt:
    ckpt.restore_training_state(model, optimizer, scheduler)
"""

from __future__ import annotations

from cortexflow.checkpoint import checkpoint, resume, Checkpoint, get_job_id
from cortexflow.experiment import init
from cortexflow.mlflow_util import (
    log_metric,
    log_metrics,
    log_params,
    log_artifact,
    get_mlflow_client,
)
from cortexflow.ray_util import (
    remote,
    get,
    get_ray_client,
    status,
    result,
    logs,
    Job,
    JobInfo,
)
from cortexflow.s3_util import upload, upload_dir, download, get_s3_client


__all__ = [
    "init",
    # Ray / jobs
    "remote",
    "get",
    "get_ray_client",
    "status",
    "result",
    "logs",
    "Job",
    "JobInfo",
    # MLflow
    "mlflow_run",
    "log_metric",
    "log_metrics",
    "log_params",
    "log_artifact",
    "get_mlflow_client",
    # S3
    "upload",
    "upload_dir",
    "download",
    "get_s3_client",
    # Checkpointing
    "checkpoint",
    "resume",
    "Checkpoint",
    "get_job_id",
]
