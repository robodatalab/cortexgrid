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

## Switching to AWS S3

To migrate artifact storage to real AWS S3, update your `.env` file:

```bash
# Comment out the local MinIO settings:
# ARTIFACT_STORE_ENDPOINT=http://minio:9000
# ARTIFACT_STORE_BUCKET=mlflow-artifacts
# ARTIFACT_STORE_ACCESS_KEY=minioadmin
# ARTIFACT_STORE_SECRET_KEY=minioadmin

# Use these instead:
ARTIFACT_STORE_ENDPOINT=                          # empty = real AWS S3
ARTIFACT_STORE_BUCKET=your-s3-bucket-name
ARTIFACT_STORE_ACCESS_KEY=<AWS_ACCESS_KEY_ID>     # or use instance role
ARTIFACT_STORE_SECRET_KEY=<AWS_SECRET_ACCESS_KEY>
```

Then restart the MLflow service:

```bash
docker compose restart mlflow
```

**No code changes are needed.** All storage config is read from environment variables, injected automatically by `cortexflow.init()`.

## Using Instance Roles (Recommended for AWS)

If running on EC2 with an IAM instance role, you can leave `ARTIFACT_STORE_ACCESS_KEY` and `ARTIFACT_STORE_SECRET_KEY` empty. The AWS SDK (boto3) will automatically use the instance role credentials.
