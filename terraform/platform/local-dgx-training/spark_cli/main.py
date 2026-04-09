"""spark — submit and monitor ML jobs on the DGX Spark cluster.

Usage:
    spark train.py                         # submit from current dir
    spark train.py --gpus 1 --follow       # request GPU, tail logs
    spark train.py --pip transformers      # add runtime deps
    spark status                           # show cluster & job status
    spark logs <job-id>                    # tail logs for a job
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

from ray.job_submission import JobSubmissionClient


def get_ray_client() -> tuple[JobSubmissionClient, str]:
    """Connect to Ray and return (client, dgx_ip)."""
    ray_address = os.environ.get("RAY_ADDRESS")
    dgx_ip = os.environ.get("DGX_TAILSCALE_IP", "localhost")

    if not ray_address:
        if dgx_ip and dgx_ip != "localhost":
            ray_address = f"http://{dgx_ip}:8265"
        else:
            print("Error: RAY_ADDRESS not set.", file=sys.stderr)
            print("  Run 'make setup-mac' in the infra repo, then 'source ~/.zshrc'", file=sys.stderr)
            sys.exit(1)

    try:
        client = JobSubmissionClient(ray_address)
    except Exception as e:
        print(f"Error: Cannot connect to Ray at {ray_address}", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    return client, dgx_ip


def cmd_submit(args: argparse.Namespace) -> None:
    """Submit a job to the Ray cluster."""
    client, dgx_ip = get_ray_client()

    runtime_env: dict = {
        "working_dir": args.working_dir or ".",
    }

    if args.pip:
        runtime_env["pip"] = args.pip

    env_vars: dict[str, str] = {}
    mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    if mlflow_uri:
        env_vars["MLFLOW_TRACKING_URI"] = mlflow_uri
    s3_endpoint = os.environ.get("MLFLOW_S3_ENDPOINT_URL", "")
    if s3_endpoint:
        env_vars["MLFLOW_S3_ENDPOINT_URL"] = s3_endpoint
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        val = os.environ.get(key, "")
        if val:
            env_vars[key] = val
    if args.experiment:
        env_vars["MLFLOW_EXPERIMENT_NAME"] = args.experiment
    if env_vars:
        runtime_env["env_vars"] = env_vars

    entrypoint = f"python {args.script}"

    metadata = {}
    if args.name:
        metadata["job_name"] = args.name

    job_id = client.submit_job(
        entrypoint=entrypoint,
        runtime_env=runtime_env,
        metadata=metadata,
    )

    print(f"Job submitted!")
    print(f"  ID:        {job_id}")
    if args.name:
        print(f"  Name:      {args.name}")
    print(f"  Dashboard: http://{dgx_ip}:8265/#/job/{job_id}")

    if args.follow:
        time.sleep(2)
        _tail_logs(client, job_id)


def cmd_status(args: argparse.Namespace) -> None:
    """Show cluster and job status."""
    client, dgx_ip = get_ray_client()

    jobs = client.list_jobs()
    if not jobs:
        print("No jobs.\n")
    else:
        groups: dict[str, list] = {
            "RUNNING": [], "PENDING": [], "FAILED": [],
            "SUCCEEDED": [], "STOPPED": [],
        }
        for job in jobs:
            status = job.status.value if hasattr(job.status, "value") else str(job.status)
            groups.get(status, groups["PENDING"]).append(job)

        icons = {"RUNNING": ">>", "PENDING": "..", "FAILED": "!!", "SUCCEEDED": "OK", "STOPPED": "--"}
        for status in ["RUNNING", "PENDING", "FAILED", "SUCCEEDED", "STOPPED"]:
            for job in groups[status]:
                name = ""
                if job.metadata and "job_name" in job.metadata:
                    name = f"  {job.metadata['job_name']}"
                duration = _format_duration(getattr(job, "start_time", None), getattr(job, "end_time", None))
                print(f"  [{icons[status]}] {job.submission_id}{name}  {duration}")

    print()
    print(f"  Ray:     http://{dgx_ip}:8265")
    print(f"  MLflow:  http://{dgx_ip}:5000")
    print(f"  Grafana: http://{dgx_ip}:3000")


def cmd_logs(args: argparse.Namespace) -> None:
    """Tail logs for a specific job."""
    client, _ = get_ray_client()
    _tail_logs(client, args.job_id)


def _tail_logs(client: JobSubmissionClient, job_id: str) -> None:
    try:
        for line in client.tail_job_logs(job_id):
            print(line, end="")
    except KeyboardInterrupt:
        print(f"\nStopped tailing. Job {job_id} may still be running.")


def _format_duration(start_ms: int | None, end_ms: int | None = None) -> str:
    if start_ms is None:
        return ""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    end = end_ms if end_ms else now_ms
    s = (end - start_ms) / 1000
    if s < 60:
        return f"{s:.0f}s"
    elif s < 3600:
        return f"{s / 60:.1f}m"
    return f"{s / 3600:.1f}h"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="spark",
        description="Submit and monitor ML jobs on the DGX Spark cluster",
    )
    sub = parser.add_subparsers(dest="command")

    # spark <script.py> (default: submit)
    p_run = sub.add_parser("run", help="Submit a job (default command)")
    _add_submit_args(p_run)

    # spark status
    sub.add_parser("status", help="Show cluster and job status")

    # spark logs <job-id>
    p_logs = sub.add_parser("logs", help="Tail logs for a job")
    p_logs.add_argument("job_id", help="Ray job ID")

    args = parser.parse_args()

    # If no subcommand but first arg looks like a .py file, treat as submit
    if args.command is None:
        if len(sys.argv) > 1 and sys.argv[1].endswith(".py"):
            # Re-parse as a submit command
            p_submit = argparse.ArgumentParser(prog="spark")
            _add_submit_args(p_submit)
            args = p_submit.parse_args()
            args.command = "run"
        else:
            parser.print_help()
            sys.exit(1)

    if args.command == "run":
        cmd_submit(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "logs":
        cmd_logs(args)


def _add_submit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("script", help="Python script to run")
    parser.add_argument("--working-dir", "-d", default=None, help="Working directory (default: current dir)")
    parser.add_argument("--pip", nargs="*", default=None, help="Additional pip packages for the job")
    parser.add_argument("--gpus", type=int, default=0, help="Number of GPUs to request")
    parser.add_argument("--name", "-n", default=None, help="Human-readable job name")
    parser.add_argument("--experiment", "-e", default=None, help="MLflow experiment name")
    parser.add_argument("--follow", "-f", action="store_true", help="Tail logs after submission")


if __name__ == "__main__":
    main()
