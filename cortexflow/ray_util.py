"""Query Ray cluster for job status and logs."""

from __future__ import annotations

from ray.job_submission import JobSubmissionClient

from cortexflow.experiment import Experiment


def get_ray_status(experiment: Experiment, ray_job_id: str) -> str:
    """Return the current status of a previously submitted ray job."""
    client = JobSubmissionClient(experiment.ray_address)
    return client.get_job_status(ray_job_id).value


def get_ray_logs(experiment: Experiment, ray_job_id: str) -> str:
    """Return the stdout/stderr of a previously submitted ray job."""
    client = JobSubmissionClient(experiment.ray_address)
    return client.get_job_logs(ray_job_id)
