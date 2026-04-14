"""Query Ray cluster for job status and logs."""

from __future__ import annotations

from ray.job_submission import JobSubmissionClient

from cortexflow.infra import get_ray_job_server_uri, get_server_ip


def get_ray_status(ray_job_id: str) -> str:
    """Return the current status of a previously submitted ray job."""
    client = JobSubmissionClient(get_ray_job_server_uri())
    return client.get_job_status(ray_job_id).value


def get_ray_logs(ray_job_id: str) -> str:
    """Return the stdout/stderr of a previously submitted ray job."""
    client = JobSubmissionClient(get_ray_job_server_uri())
    return client.get_job_logs(ray_job_id)


def get_ray_job_url(ray_job_id: str) -> str:
    """Build the URL to view a job in the Ray dashboard (always via DGX tailscale IP)."""
    server_ip = get_server_ip()
    return f"http://{server_ip}:8265/#/jobs/{ray_job_id}"


def stop_ray_job(ray_job_id: str) -> None:
    """Stop a running ray job."""
    client = JobSubmissionClient(get_ray_job_server_uri())
    client.stop_job(ray_job_id)


def submit_ray_job(
    submission_id: str,
    entrypoint: str,
    runtime_env: dict,
    num_gpus: int = 0,
    num_cpus: int = 1,
) -> None:
    """Submit a job to Ray with a caller-supplied deterministic submission_id.

    Raises whatever the Ray SDK raises on a duplicate submission_id; the
    control plane relies on that exception to short-circuit re-submission
    on retry paths.
    """
    client = JobSubmissionClient(get_ray_job_server_uri())
    client.submit_job(
        submission_id=submission_id,
        entrypoint=entrypoint,
        runtime_env=runtime_env,
        entrypoint_num_gpus=num_gpus,
        entrypoint_num_cpus=num_cpus,
    )
