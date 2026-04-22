"""cortexflow — connect your code to the RoboLab compute cluster.

import cortexflow

cortexflow.init()

# Fire-and-forget training on the DGX; returns a job id immediately.
# The jobs control plane picks up the submission and dispatches it to Ray.
job_id = cortexflow.remote(my_train, config, num_gpus=1, retry=True)
print(f"Submitted: {job_id}")

# Check on it later
for job in cortexflow.list_experiment_run_jobs(run_id):
    status = cortexflow.get_ray_job_status(job.get_ray_job_id())

# Inside the training function — checkpoint after each epoch
# (checkpointing requires the `training` extras: `pip install cortexflow[training]`)
from cortexflow.checkpoint import checkpoint, resume

with checkpoint() as ckpt:
    ckpt.epoch = epoch
    ckpt.save_training_state(model, optimizer, scheduler)

# On resume — load checkpoint if it exists
ckpt = resume()
if ckpt:
    ckpt.restore_training_state(model, optimizer, scheduler)
"""

from __future__ import annotations

from typing import Any, Callable

from cortexflow.experiment import (
    Experiment,
    list_experiments,
)
from cortexflow.infra import get_ray_job_server_uri
from cortexflow.jobs import (
    schedule_remote_job,
    list_experiment_run_jobs,
    stop_experiment_run_jobs,
    JobLifecycle,
    LifecycleEvent,
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
from cortexflow.ray_util import (
    get_ray_status,
    get_ray_logs,
    get_ray_job_url,
    stop_ray_job,
    submit_ray_job,
    list_ray_jobs_with_submission_id,
    get_ray_job_status,
    ray_submission_id,
    get_ray_job_attempt,
    JobStatus,
)
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
    "get_ray_job_status",
    "list_experiment_run_jobs",
    "stop_experiment_run_jobs",
    "JobStatus",
    "JobLifecycle",
    "LifecycleEvent",
    "Payload",
    "get_ray_status",
    "get_ray_logs",
    "get_ray_job_url",
    "stop_ray_job",
    "submit_ray_job",
    "get_ray_job_server_uri",
    "list_ray_jobs_with_submission_id",
    "ray_submission_id",
    "get_ray_job_attempt",
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
]
