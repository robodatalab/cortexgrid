#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# DGX Spark — One-time setup script
# =============================================================================
# Idempotent: safe to run multiple times.
#
# Prerequisites:
#   - .env file must already exist (run `make setup-mac` on your Mac first,
#     then copy .env to the DGX via scp or git)

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
echo "[1/5] Checking Docker..."
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
echo "[2/5] Checking NVIDIA Container Toolkit..."
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

# --- Step 3: Check .env ---
echo "[3/5] Checking .env..."
if [[ ! -f .env ]]; then
    echo "Error: .env not found."
    echo ""
    echo "  Run 'make setup-mac' on your Mac first, then copy .env to the DGX:"
    echo "    scp .env dgx:$(pwd)/.env"
    echo ""
    echo "  Or copy .env.example and fill in values manually:"
    echo "    cp .env.example .env"
    exit 1
fi
echo "  .env found — OK"

# Validate required values are present
source .env
missing=()
[[ -z "${DGX_TAILSCALE_IP:-}" || "${DGX_TAILSCALE_IP}" == "100.x.x.x" ]] && missing+=("DGX_TAILSCALE_IP")
[[ -z "${POSTGRES_PASSWORD:-}" ]] && missing+=("POSTGRES_PASSWORD")
[[ -z "${MINIO_ROOT_PASSWORD:-}" ]] && missing+=("MINIO_ROOT_PASSWORD")
[[ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]] && missing+=("GRAFANA_ADMIN_PASSWORD")

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Error: The following values are missing in .env:"
    for var in "${missing[@]}"; do
        echo "  - $var"
    done
    exit 1
fi
echo "  All required values present"

# --- Step 4: Pull images, build, and start ---
echo "[4/5] Building and starting services..."
docker compose pull --ignore-pull-failures 2>/dev/null || true
docker compose up -d --build

# --- Step 5: Wait for Ray Dashboard ---
echo "[5/5] Waiting for Ray Dashboard..."
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
echo "  Next: Run 'make setup-mac' on your Mac to configure the client."
echo
