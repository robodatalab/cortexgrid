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

from typing import Any, Callable

from cortexflow.checkpoint import checkpoint, resume, Checkpoint, get_cortexflow_job_id
from cortexflow.experiment import (
    Experiment,
    list_experiments,
)
from cortexflow.infra import get_ray_job_server_uri, set_runs_on_server
from cortexflow.jobs import (
    schedule_remote_job,
    get_job_status,
    list_experiment_run_jobs,
    stop_experiment_run_jobs,
    JobStatus,
    JobLifecycle,
    Payload,
)
from cortexflow.mlflow_util import (
    log_metric,
    log_metrics,
    log_params,
    log_artifact,
    get_mlflow_client,
    list_run_metrics,
    get_metric_history,
    list_run_params,
    list_run_artifacts,
)
from cortexflow.ray_util import get_ray_status, get_ray_logs, get_ray_job_url
from cortexflow.s3_util import upload, upload_dir, download, get_s3_client


def remote(
    fn: Callable[..., Any],
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    retry: bool = False,
    **kwargs: Any,
) -> str:
    """Submit a function to the control plane. Returns a job ID."""
    experiment = Experiment.get_instance()
    return schedule_remote_job(
        experiment.experiment_name,
        experiment.run_id,
        fn,
        *args,
        num_gpus=num_gpus,
        num_cpus=num_cpus,
        retry=retry,
        **kwargs,
    )


__all__ = [
    "Experiment",
    # Ray / jobs
    "remote",
    "get_job_status",
    "list_experiment_run_jobs",
    "stop_experiment_run_jobs",
    "JobStatus",
    "JobLifecycle",
    "Payload",
    "get_ray_status",
    "get_ray_logs",
    "get_ray_job_url",
    "get_ray_job_server_uri",
    "set_runs_on_server",
    # MLflow
    "log_metric",
    "log_metrics",
    "log_params",
    "log_artifact",
    "get_mlflow_client",
    "list_run_metrics",
    "get_metric_history",
    "list_run_params",
    "list_run_artifacts",
    "list_experiments",
    # S3
    "upload",
    "upload_dir",
    "download",
    "get_s3_client",
    # Checkpointing
    "checkpoint",
    "resume",
    "Checkpoint",
    "get_cortexflow_job_id",
]
