"""Query Ray cluster for job status and logs."""

from __future__ import annotations

from ray.job_submission import JobSubmissionClient

from cortexflow.experiment import SM_PREFIX, get_ray_address
from cortexflow.secrets import get_secret


def get_ray_status(ray_job_id: str) -> str:
    """Return the current status of a previously submitted ray job."""
    client = JobSubmissionClient(get_ray_address())
    return client.get_job_status(ray_job_id).value


def get_ray_logs(ray_job_id: str) -> str:
    """Return the stdout/stderr of a previously submitted ray job."""
    client = JobSubmissionClient(get_ray_address())
    return client.get_job_logs(ray_job_id)


def get_ray_job_url(ray_job_id: str) -> str:
    """Build the URL to view a job in the Ray dashboard (always via DGX tailscale IP)."""
    dgx_ip = get_secret(f"{SM_PREFIX}/DGX_TAILSCALE_IP")
    return f"http://{dgx_ip}:8265/#/jobs/{ray_job_id}"
