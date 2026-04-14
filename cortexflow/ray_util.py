"""Query Ray cluster for job status and logs."""

from __future__ import annotations

from enum import Enum

from cortexflow.infra import get_ray_job_server_uri, get_server_ip
from ray.job_submission import JobSubmissionClient


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    STOPPED = "stopped"


def get_ray_status(ray_job_id: str | None) -> str | None:
    """Return the current status of a previously submitted ray job."""
    if ray_job_id is None:
        return None

    client = JobSubmissionClient(get_ray_job_server_uri())
    return client.get_job_status(ray_job_id).value


def get_ray_job_status(ray_job_id: str | None) -> JobStatus:
    """Derive a job's observable status from a live Ray query.

    Returns ``PENDING`` both when ``ray_job_id is None`` (never submitted)
    and when Ray itself reports ``PENDING`` (queued). Callers that need
    to distinguish those two must check ``ray_job_id is None`` first.
    """
    ray_status = get_ray_status(ray_job_id)
    if ray_job_id is None or ray_status == "PENDING":
        return JobStatus.PENDING
    if ray_status == "SUCCEEDED":
        return JobStatus.FINISHED
    if ray_status == "FAILED":
        return JobStatus.FAILED
    if ray_status == "STOPPED":
        return JobStatus.STOPPED
    return JobStatus.RUNNING


def get_ray_logs(ray_job_id: str | None) -> str | None:
    """Return the stdout/stderr of a previously submitted ray job."""
    if ray_job_id is None:
        return None

    client = JobSubmissionClient(get_ray_job_server_uri())
    return client.get_job_logs(ray_job_id)


def get_ray_job_url(ray_job_id: str | None) -> str | None:
    """Build the URL to view a job in the Ray dashboard (always via DGX tailscale IP)."""
    if ray_job_id is None:
        return None

    server_ip = get_server_ip()
    return f"http://{server_ip}:8265/#/jobs/{ray_job_id}"


def stop_ray_job(ray_job_id: str) -> None:
    """Stop a running ray job."""
    client = JobSubmissionClient(get_ray_job_server_uri())
    client.stop_job(ray_job_id)


def list_ray_jobs_with_submission_id() -> list[str]:
    """List all ray jobs, the ones that received submission id."""
    client = JobSubmissionClient(get_ray_job_server_uri())
    return [
        job.submission_id for job in client.list_jobs() if job.submission_id is not None
    ]


def ray_submission_id(run_id: str, job_id: str, attempt: int | None) -> str:
    """Deterministic Ray submission id derived from a job's identity."""
    return (
        f"{run_id}-{job_id}-{attempt}" if attempt is not None else f"{run_id}-{job_id}"
    )


def get_ray_job_attempt(ray_job_id: str | None) -> int:
    if ray_job_id is None:
        return 0
    _, sep, suffix = ray_job_id.rpartition("-")
    if not sep or not suffix.isdigit():
        raise ValueError(f"Ray submission_id has no attempt suffix: {ray_job_id!r}")
    return int(suffix)


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
