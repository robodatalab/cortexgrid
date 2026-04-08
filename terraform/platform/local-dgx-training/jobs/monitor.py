#!/usr/bin/env python3
"""CLI tool to monitor the Ray cluster and job status from a Mac workstation.

Usage:
    python jobs/monitor.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from ray.job_submission import JobSubmissionClient, JobStatus


def get_repo_root() -> Path:
    """Return the repo root (local-dgx-training/)."""
    return Path(__file__).resolve().parent.parent


def format_duration(start_ms: int | None, end_ms: int | None = None) -> str:
    """Format duration from timestamps in milliseconds."""
    if start_ms is None:
        return "N/A"
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    end = end_ms if end_ms else now_ms
    duration_s = (end - start_ms) / 1000

    if duration_s < 60:
        return f"{duration_s:.0f}s"
    elif duration_s < 3600:
        return f"{duration_s / 60:.1f}m"
    else:
        return f"{duration_s / 3600:.1f}h"


def print_jobs(client: JobSubmissionClient) -> None:
    """Print all jobs grouped by status."""
    jobs = client.list_jobs()
    if not jobs:
        print("  No jobs found.\n")
        return

    # Group by status
    groups: dict[str, list] = {
        "RUNNING": [],
        "PENDING": [],
        "FAILED": [],
        "STOPPED": [],
        "SUCCEEDED": [],
    }

    for job in jobs:
        status = job.status.value if hasattr(job.status, "value") else str(job.status)
        bucket = status if status in groups else "PENDING"
        groups[bucket].append(job)

    # Print active jobs first
    for status in ["RUNNING", "PENDING", "FAILED", "SUCCEEDED", "STOPPED"]:
        job_list = groups[status]
        if not job_list:
            continue

        status_icons = {
            "RUNNING": "[RUN]",
            "PENDING": "[PND]",
            "FAILED": "[FAIL]",
            "SUCCEEDED": "[OK]",
            "STOPPED": "[STOP]",
        }

        print(f"  {status_icons.get(status, '[???]')} {status} ({len(job_list)}):")
        for job in job_list:
            name = ""
            if job.metadata and "job_name" in job.metadata:
                name = f" ({job.metadata['job_name']})"
            start_time = getattr(job, "start_time", None)
            end_time = getattr(job, "end_time", None)
            duration = format_duration(start_time, end_time)
            print(f"      {job.submission_id}{name}  [{duration}]")
        print()


def main() -> None:
    load_dotenv(get_repo_root() / ".env")

    ray_address = os.environ.get("RAY_ADDRESS")
    dgx_ip = os.environ.get("DGX_TAILSCALE_IP", "localhost")

    if not ray_address:
        if dgx_ip:
            ray_address = f"http://{dgx_ip}:8265"
        else:
            print("Error: RAY_ADDRESS not set. Run scripts/setup-mac.sh first.", file=sys.stderr)
            sys.exit(1)

    print("=" * 60)
    print("  RoboLab ML Infrastructure — Status")
    print("=" * 60)
    print()

    # Connect to Ray
    try:
        client = JobSubmissionClient(ray_address)
    except Exception as e:
        print(f"Error: Cannot connect to Ray at {ray_address}")
        print(f"  {e}")
        print(f"\n  Is the DGX Spark running? Check with: scripts/health-check.sh")
        sys.exit(1)

    # Jobs
    print("Jobs:")
    print_jobs(client)

    # Service URLs
    print("Service URLs:")
    print(f"  Ray Dashboard:  http://{dgx_ip}:8265")
    print(f"  MLflow UI:      http://{dgx_ip}:5000")
    print(f"  Grafana:        http://{dgx_ip}:3000")
    print(f"  MinIO Console:  http://{dgx_ip}:9001")
    print()


if __name__ == "__main__":
    main()
