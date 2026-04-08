#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# DGX Spark — One-time setup script
# =============================================================================
# Idempotent: safe to run multiple times.
#
# Prerequisites:
#   - AWS CLI configured with the robolab-dgx IAM user credentials
#     (aws configure --profile robolab)
#   - Secrets already provisioned via terraform/platform/secrets/

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Validate working directory
if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
    echo "Error: This script must be run from the local-dgx-training directory."
    echo "  cd terraform/platform/local-dgx-training && scripts/setup-dgx.sh"
    exit 1
fi

cd "$REPO_ROOT"

echo "=========================================="
echo "  RoboLab ML Infrastructure — DGX Setup"
echo "=========================================="
echo

# --- Step 1: Check Docker ---
echo "[1/7] Checking Docker..."
if ! command -v docker &>/dev/null; then
    echo "Error: Docker is not installed."
    echo "  Install: https://docs.docker.com/engine/install/ubuntu/"
    exit 1
fi

if ! docker compose version &>/dev/null; then
    echo "Error: Docker Compose V2 is not installed."
    echo "  Install: https://docs.docker.com/compose/install/"
    exit 1
fi
echo "  Docker $(docker --version | awk '{print $3}') — OK"
echo "  Docker Compose $(docker compose version --short) — OK"

# --- Step 2: Check NVIDIA Container Toolkit ---
echo "[2/7] Checking NVIDIA Container Toolkit..."
if ! command -v nvidia-smi &>/dev/null; then
    echo "Warning: nvidia-smi not found. GPU workloads may not work."
    echo "  Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
else
    echo "  $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1) — OK"
fi

if ! docker info 2>/dev/null | grep -q "nvidia"; then
    echo "Warning: NVIDIA runtime not detected in Docker."
    echo "  Ensure nvidia-container-toolkit is installed and Docker is configured."
fi

# --- Step 3: Check AWS CLI ---
echo "[3/7] Checking AWS CLI..."
if ! command -v aws &>/dev/null; then
    echo "Error: AWS CLI is not installed."
    echo "  Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi
echo "  aws CLI $(aws --version | awk '{print $1}') — OK"

# Verify AWS credentials can reach Secrets Manager
if ! aws secretsmanager get-secret-value --secret-id "robolab/infra/dgx-tailscale-ip" --query 'SecretString' --output text &>/dev/null; then
    echo "Error: Cannot read secrets from AWS Secrets Manager."
    echo "  Configure credentials: aws configure"
    echo "  Use the robolab-dgx IAM user credentials from terraform output."
    exit 1
fi
echo "  AWS credentials valid — can read secrets"

# --- Step 4: Pull secrets and generate .env ---
echo "[4/7] Pulling secrets from AWS Secrets Manager..."
bash "$REPO_ROOT/scripts/pull-secrets.sh"

# --- Step 5: Pull images ---
echo "[5/7] Pulling Docker images..."
docker compose pull --ignore-pull-failures 2>/dev/null || true

# --- Step 6: Build and start ---
echo "[6/7] Building and starting services..."
docker compose up -d --build

# --- Step 7: Wait for Ray Dashboard ---
echo "[7/7] Waiting for Ray Dashboard..."
MAX_WAIT=120
ELAPSED=0
while ! curl -sf http://localhost:8265 &>/dev/null; do
    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
        echo "  Warning: Ray Dashboard not reachable after ${MAX_WAIT}s"
        echo "  Check logs: docker compose logs ray-head"
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
    echo "  Waiting... (${ELAPSED}s)"
done

if curl -sf http://localhost:8265 &>/dev/null; then
    echo "  Ray Dashboard is ready!"
fi

# --- Summary ---
source .env 2>/dev/null || true
DGX_IP="${DGX_TAILSCALE_IP:-localhost}"

echo
echo "=========================================="
echo "  Setup Complete!"
echo "=========================================="
echo
echo "  Service             URL"
echo "  ─────────────────── ──────────────────────────────────"
echo "  Ray Dashboard       http://${DGX_IP}:8265"
echo "  MLflow UI           http://${DGX_IP}:5000"
echo "  Grafana             http://${DGX_IP}:3000"
echo "  MinIO Console       http://${DGX_IP}:9001"
echo "  Prometheus          http://${DGX_IP}:9090"
echo
echo "  Next: Run scripts/setup-mac.sh on your Mac to configure the client."
echo
