"""Entry point: python -m jobs_control_plane

Reads config from env vars — designed for Docker deployment where services
communicate via internal hostnames rather than Tailscale IPs from AWS SM.
"""

from __future__ import annotations

import logging
import os
import sys

from cortexflow.experiment import Experiment, set_instance
from jobs_control_plane.server import run


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    experiment_name = os.environ.get("CORTEXFLOW_EXPERIMENT")
    run_id = os.environ.get("CORTEXFLOW_RUN_ID")

    if not experiment_name or not run_id:
        print(
            "Set CORTEXFLOW_EXPERIMENT and CORTEXFLOW_RUN_ID environment variables",
            file=sys.stderr,
        )
        sys.exit(1)

    experiment = Experiment(
        experiment_name=experiment_name,
        run_id=run_id,
        ray_address=os.environ.get("RAY_ADDRESS", "http://ray-head:8265"),
        dgx_ip="",
        mlflow_tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000"),
        mlflow_s3_endpoint_url=os.environ.get("MLFLOW_S3_ENDPOINT_URL", ""),
        s3_endpoint_url=os.environ.get("MLFLOW_S3_ENDPOINT_URL", ""),
        s3_access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
        s3_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        s3_default_bucket=os.environ.get("ARTIFACT_STORE_BUCKET", "ray-checkpoints"),
        github_token="",
    )
    set_instance(experiment)
    run(experiment)


if __name__ == "__main__":
    main()
