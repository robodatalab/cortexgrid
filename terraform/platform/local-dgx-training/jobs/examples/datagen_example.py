#!/usr/bin/env python3
"""Example: Parallel data generation / preprocessing job.

Processes a list of input files in parallel using Ray tasks, writes output to
MinIO (or S3 on AWS) via boto3, and tracks progress in MLflow.

Usage:
    python jobs/submit.py \\
        --script jobs/examples/datagen_example.py \\
        --working-dir . \\
        --env pytorch \\
        --name "datagen-example" \\
        --mlflow-experiment "data-processing"
"""

from __future__ import annotations

import argparse
import io
import json
import os
import time

import boto3
import mlflow
import ray


@ray.remote(max_retries=2, retry_exceptions=True)
def process_file(
    file_key: str,
    output_bucket: str,
    s3_endpoint: str,
    access_key: str,
    secret_key: str,
) -> dict[str, str | int]:
    """Process a single input file and write results to object storage.

    This is a placeholder — replace with your actual data processing logic.
    """
    # Configure boto3 for MinIO or S3
    s3_kwargs: dict = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
    }
    if s3_endpoint:
        s3_kwargs["endpoint_url"] = s3_endpoint

    s3 = boto3.client("s3", **s3_kwargs)

    # Simulate processing
    processed_data = {
        "source": file_key,
        "records_processed": 1000,
        "timestamp": time.time(),
    }

    # Write output
    output_key = f"processed/{file_key.replace('/', '_')}.json"
    s3.put_object(
        Bucket=output_bucket,
        Key=output_key,
        Body=io.BytesIO(json.dumps(processed_data).encode()),
        ContentType="application/json",
    )

    return {"input": file_key, "output": output_key, "records": 1000}


def main() -> None:
    parser = argparse.ArgumentParser(description="Parallel data generation example")
    parser.add_argument(
        "--input-files",
        nargs="*",
        default=[f"raw/data_part_{i:03d}.csv" for i in range(10)],
        help="Input file keys to process",
    )
    parser.add_argument("--output-bucket", default=None, help="Output bucket name")
    args = parser.parse_args()

    ray.init()

    # Read storage config from environment (injected by submit.py)
    s3_endpoint = os.environ.get("MLFLOW_S3_ENDPOINT_URL", "")
    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    output_bucket = args.output_bucket or os.environ.get("ARTIFACT_STORE_BUCKET", "ray-checkpoints")

    # Set up MLflow tracking
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)

    experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "data-processing")
    mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name="datagen-example") as run:
        mlflow.log_param("num_files", len(args.input_files))
        mlflow.log_param("output_bucket", output_bucket)

        # Submit all file processing tasks in parallel
        futures = [
            process_file.remote(
                file_key=f,
                output_bucket=output_bucket,
                s3_endpoint=s3_endpoint,
                access_key=access_key,
                secret_key=secret_key,
            )
            for f in args.input_files
        ]

        # Collect results and track progress
        total_records = 0
        completed = 0

        for future in futures:
            result = ray.get(future)
            completed += 1
            total_records += result["records"]

            mlflow.log_metric("data_files_processed", completed, step=completed)
            print(f"[{completed}/{len(args.input_files)}] Processed {result['input']} → {result['output']}")

        mlflow.log_metric("total_records_processed", total_records)
        print(f"\nDone. Processed {total_records} records across {completed} files.")
        print(f"MLflow run ID: {run.info.run_id}")


if __name__ == "__main__":
    main()
