#!/bin/sh
set -euo pipefail

# Wait for MinIO to be ready
echo "Waiting for MinIO to be ready..."
until mc alias set myminio http://minio:9000 "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" 2>/dev/null; do
  echo "  MinIO not ready, retrying in 2s..."
  sleep 2
done

echo "MinIO is ready. Creating buckets..."

# Create buckets (ignore error if they already exist)
mc mb --ignore-existing myminio/mlflow-artifacts
mc mb --ignore-existing myminio/ray-checkpoints

echo "Buckets created successfully:"
mc ls myminio/
