# Storage Configuration

## Overview

This infrastructure uses S3-compatible storage for ML artifacts (model checkpoints, datasets, logs). Locally, **MinIO** provides an S3-compatible API. On AWS, the same code works with real **S3** — only environment variables change.

## Local Setup (MinIO on DGX Spark)

MinIO runs as a Docker container and provides:
- **S3 API** on port `9000`
- **Web Console** on port `9001`

Two buckets are created automatically on first run:
- `mlflow-artifacts` — MLflow experiment artifacts, model checkpoints
- `ray-checkpoints` — Ray Train checkpoint storage

