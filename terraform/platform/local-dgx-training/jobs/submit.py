#!/usr/bin/env python3
"""CLI tool to submit ML jobs to the Ray cluster from a Mac workstation.

Usage:
    python jobs/submit.py \\
        --script train.py \\
        --working-dir ./experiments/my_experiment \\
        --env transformers \\
        --gpus 1 \\
        --name "finetune-llama-v1" \\
        --mlflow-experiment "llama-experiments" \\
        --follow
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from ray.job_submission import JobSubmissionClient, JobStatus


def get_repo_root() -> Path:
    """Return the repo root (local-dgx-training/)."""
    return Path(__file__).resolve().parent.parent


def load_runtime_env(env_name: str, extra_pip: list[str] | None = None) -> dict:
    """Load a runtime environment YAML and optionally extend its pip deps."""
    env_file = get_repo_root() / "ray" / "runtime_envs" / f"{env_name}.yaml"
    if not env_file.exists():
        print(f"Error: runtime env '{env_name}' not found at {env_file}", file=sys.stderr)
        sys.exit(1)

    with open(env_file) as f:
        runtime_env = yaml.safe_load(f)

    if extra_pip:
        runtime_env.setdefault("pip", []).extend(extra_pip)

    return runtime_env


def build_env_vars() -> dict[str, str]:
    """Build environment variables to inject into the Ray job."""
    load_dotenv(get_repo_root() / ".env")

    dgx_ip = os.environ.get("DGX_TAILSCALE_IP", "")
    return {
        "MLFLOW_TRACKING_URI": os.environ.get("MLFLOW_TRACKING_URI", f"http://{dgx_ip}:5000"),
        "MLFLOW_S3_ENDPOINT_URL": os.environ.get("ARTIFACT_STORE_ENDPOINT", f"http://{dgx_ip}:9000"),
        "AWS_ACCESS_KEY_ID": os.environ.get("ARTIFACT_STORE_ACCESS_KEY", ""),
        "AWS_SECRET_ACCESS_KEY": os.environ.get("ARTIFACT_STORE_SECRET_KEY", ""),
    }


def tail_logs(client: JobSubmissionClient, job_id: str) -> None:
    """Tail job logs until completion."""
    print(f"\n--- Tailing logs for {job_id} ---\n")
    try:
        for line in client.tail_job_logs(job_id):
            print(line, end="")
    except KeyboardInterrupt:
        print(f"\n\nStopped tailing. Job {job_id} is still running.")
        print(f"Resume with: python jobs/monitor.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit a job to the Ray cluster")
    parser.add_argument("--script", required=True, help="Python script to run (relative to --working-dir)")
    parser.add_argument("--working-dir", required=True, help="Working directory to upload to the cluster")
    parser.add_argument("--env", default=None, help="Runtime environment name (e.g. 'pytorch', 'transformers')")
    parser.add_argument("--gpus", type=int, default=0, help="Number of GPUs to request")
    parser.add_argument("--name", default=None, help="Human-readable job name")
    parser.add_argument("--mlflow-experiment", default=None, help="MLflow experiment name to set")
    parser.add_argument("--extra-pip", nargs="*", default=None, help="Additional pip packages")
    parser.add_argument("--follow", action="store_true", help="Tail logs after submission")
    args = parser.parse_args()

    load_dotenv(get_repo_root() / ".env")

    ray_address = os.environ.get("RAY_ADDRESS")
    if not ray_address:
        dgx_ip = os.environ.get("DGX_TAILSCALE_IP", "")
        if dgx_ip:
            ray_address = f"http://{dgx_ip}:8265"
        else:
            print("Error: RAY_ADDRESS not set. Run scripts/setup-mac.sh first.", file=sys.stderr)
            sys.exit(1)

    client = JobSubmissionClient(ray_address)

    # Build runtime environment
    runtime_env: dict = {}
    if args.env:
        runtime_env = load_runtime_env(args.env, args.extra_pip)

    # Set working directory
    runtime_env["working_dir"] = args.working_dir

    # Inject environment variables
    env_vars = build_env_vars()
    if args.mlflow_experiment:
        env_vars["MLFLOW_EXPERIMENT_NAME"] = args.mlflow_experiment
    runtime_env["env_vars"] = env_vars

    # Build entrypoint
    entrypoint = f"python {args.script}"
    if args.gpus:
        # Set CUDA_VISIBLE_DEVICES via Ray resource request
        runtime_env.setdefault("env_vars", {})

    # Submit
    metadata = {}
    if args.name:
        metadata["job_name"] = args.name

    job_id = client.submit_job(
        entrypoint=entrypoint,
        runtime_env=runtime_env,
        metadata=metadata,
    )

    dgx_ip = os.environ.get("DGX_TAILSCALE_IP", "localhost")
    dashboard_url = f"http://{dgx_ip}:8265"

    print(f"Job submitted successfully!")
    print(f"  Job ID:    {job_id}")
    if args.name:
        print(f"  Name:      {args.name}")
    print(f"  Dashboard: {dashboard_url}/#/job/{job_id}")

    if args.follow:
        # Wait briefly for job to start
        time.sleep(2)
        tail_logs(client, job_id)


if __name__ == "__main__":
    main()
