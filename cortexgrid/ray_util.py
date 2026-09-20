"""Talk to the Ray cluster's dashboard: job submission + Serve applications.

Job submission goes through ray's `JobSubmissionClient`. Serve applications use
the dashboard's declarative `/api/serve/applications/` REST endpoint directly -
there's no public Python client for it that doesn't drag in `ray.init`, and the
REST surface is tiny."""

from __future__ import annotations

from enum import Enum
from typing import Any

import requests  # type: ignore

from cortexgrid.infra import (
    get_ray_job_server_uri,
    get_ray_nodes_uri,
    get_ray_serve_applications_uri,
)
from ray.job_submission import JobSubmissionClient


class JobStatus(str, Enum):
    PENDING = "pending"  # Pending scheduling
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    STOPPED = "stopped"


_ray_job_submission_client: JobSubmissionClient | None = None


def get_ray_job_submission_client() -> JobSubmissionClient:
    """Return a cached JobSubmissionClient. Each constructor call does a version-check RTT, so we share one."""
    global _ray_job_submission_client
    if _ray_job_submission_client is None:
        _ray_job_submission_client = JobSubmissionClient(get_ray_job_server_uri())
    return _ray_job_submission_client


def get_ray_job_id_for_cortexgrid_job(
    run_id: str, job_id: str, all_ray_submission_ids: list[str] | None = None
) -> str | None:
    if all_ray_submission_ids is None:
        all_ray_submission_ids = list_ray_jobs_with_submission_id()
    prefix = ray_submission_id(run_id, job_id, None) + "-"
    attempts = [sid for sid in all_ray_submission_ids if sid.startswith(prefix)]
    return max(attempts, key=get_ray_job_attempt) if attempts else None


def get_ray_status(ray_job_id: str | None) -> str | None:
    """Return the current status of a previously submitted ray job."""
    if ray_job_id is None:
        return None

    client = get_ray_job_submission_client()
    return client.get_job_status(ray_job_id).value


def to_job_status(ray_status: str | None) -> JobStatus:
    """Map one of Ray's words onto ours. `None` means never submitted."""
    if ray_status is None:
        return JobStatus.PENDING
    if ray_status == "SUCCEEDED":
        return JobStatus.FINISHED
    if ray_status == "FAILED":
        return JobStatus.FAILED
    if ray_status == "STOPPED":
        return JobStatus.STOPPED
    return JobStatus.RUNNING


def get_ray_job_status(ray_job_id: str | None) -> JobStatus:
    """Derive a job's observable status from a live Ray query.

    Returns ``PENDING`` both when ``ray_job_id is None`` (never submitted)
    and when Ray itself reports ``PENDING`` (queued). Callers that need
    to distinguish those two must check ``ray_job_id is None`` first.

    One query per job. A caller with many jobs wants
    ``list_ray_job_statuses`` instead, which answers for all of them in
    a single call.
    """
    if ray_job_id is None:
        return JobStatus.PENDING
    return to_job_status(get_ray_status(ray_job_id))


def list_ray_job_statuses() -> dict[str, JobStatus]:
    """Every ray job's status, by submission id, in one call."""
    client = get_ray_job_submission_client()
    return {
        job.submission_id: to_job_status(job.status.value)
        for job in client.list_jobs()
        if job.submission_id is not None
    }


def get_ray_logs(ray_job_id: str | None) -> str | None:
    """Return the stdout/stderr of a previously submitted ray job."""
    if ray_job_id is None:
        return None

    client = get_ray_job_submission_client()
    return client.get_job_logs(ray_job_id)


def get_ray_job_url(ray_job_id: str | None) -> str | None:
    """Build the browser-facing URL to view a job in the Ray dashboard."""
    if ray_job_id is None:
        return None

    base = get_ray_job_server_uri()
    return f"{base}/#/jobs/{ray_job_id}"


def stop_ray_job(ray_job_id: str) -> None:
    """Stop a running ray job."""
    client = get_ray_job_submission_client()
    client.stop_job(ray_job_id)


def delete_ray_job(ray_job_id: str) -> None:
    """Drop a ray job and its data from Ray's job store.

    Ray rejects the call for a job that has not settled, so callers stop
    the job and let it reach a terminal state first."""
    client = get_ray_job_submission_client()
    client.delete_job(ray_job_id)


def list_ray_jobs_with_submission_id() -> list[str]:
    """List all ray jobs, the ones that received submission id."""
    client = get_ray_job_submission_client()
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
    client = get_ray_job_submission_client()
    client.submit_job(
        submission_id=submission_id,
        entrypoint=entrypoint,
        runtime_env=runtime_env,
        entrypoint_num_gpus=num_gpus,
        entrypoint_num_cpus=num_cpus,
    )


def get_serve_details() -> dict[str, Any]:
    """GET the Serve controller's view of currently-running applications.

    Returns the full ServeInstanceDetails JSON; callers project the parts they
    care about. Raises for any non-2xx response (HTTPError carries the body).
    """
    response = requests.get(get_ray_serve_applications_uri(), timeout=30)
    response.raise_for_status()
    return response.json()


def get_ray_nodes() -> list[dict[str, Any]]:
    """GET the state API's view of the cluster's nodes.

    One dict per node, carrying at least `node_id`, `node_ip`, `state`,
    `labels` and `resources_total`. Raises for any non-2xx response
    (HTTPError carries the body).
    """
    response = requests.get(get_ray_nodes_uri(), timeout=30)
    response.raise_for_status()
    return response.json()["data"]["result"]["result"]


def put_serve_applications(applications: list[dict[str, Any]]) -> None:
    """PUT the full desired set of Serve applications.

    The endpoint is declarative: any application not in `applications` is
    deleted, any new application is created, any updated application is
    rolled. Callers that want to mutate one app should GET first, splice,
    and PUT the result.
    """
    response = requests.put(
        get_ray_serve_applications_uri(),
        json={"applications": applications},
        timeout=60,
    )
    response.raise_for_status()
