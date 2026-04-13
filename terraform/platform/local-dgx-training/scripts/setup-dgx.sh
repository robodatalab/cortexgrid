#!/usr/bin/env bash
set -euo pipefail

# DGX Spark setup — run this FROM YOUR MAC.
# Copies .env and the repo to the DGX over SSH, then starts the stack.
#
# Prerequisites:
#   - .env exists locally (run `make setup-mac` first)
#   - SSH access to the DGX over Tailscale

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
    echo "Error: Run from the local-dgx-training directory."
    exit 1
fi

cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
    echo "Error: .env not found. Run 'make setup-mac' first."
    exit 1
fi

source .env
DGX_IP="${DGX_TAILSCALE_IP:-}"
if [[ -z "$DGX_IP" || "$DGX_IP" == "100.x.x.x" ]]; then
    echo "Error: DGX_TAILSCALE_IP not set in .env"
    exit 1
fi

DGX_USER="${DGX_SSH_USER:-$(whoami)}"
DGX_HOST="${DGX_USER}@${DGX_IP}"
DGX_DIR="${DGX_REPO_PATH:-/home/${DGX_USER}/robolab-infra/terraform/platform/local-dgx-training}"

# SSH multiplexing — authenticate once, reuse for all connections
CTRL_SOCKET="/tmp/robolab-ssh-${DGX_USER}-${DGX_IP}"
SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o ControlMaster=auto -o ControlPath=${CTRL_SOCKET} -o ControlPersist=120"

cleanup() { ssh -O exit -o ControlPath="${CTRL_SOCKET}" "$DGX_HOST" 2>/dev/null || true; }
trap cleanup EXIT

echo "DGX Setup (running from Mac)"
echo "  Target: ${DGX_HOST}:${DGX_DIR}"
echo

read -s -p "DGX password: " PW
echo

echo "[1/7] Checking SSH to DGX..."
if ! SSHPASS="$PW" sshpass -e ssh $SSH_OPTS "$DGX_HOST" "echo ok" >/dev/null; then
    echo "Error: Cannot SSH to ${DGX_HOST}"
    echo "  Check Tailscale: tailscale status"
    echo "  Check SSH:       ssh ${DGX_HOST}"
    exit 1
fi
echo "  SSH connection OK"

echo "[2/7] Checking DGX prerequisites..."
ssh $SSH_OPTS "$DGX_HOST" bash -s <<'REMOTE_CHECK'
set -euo pipefail
if ! command -v docker &>/dev/null; then
    echo "Error: Docker not installed on DGX"
    exit 1
fi
if ! docker compose version &>/dev/null; then
    echo "Error: Docker Compose V2 not installed on DGX"
    exit 1
fi
echo "  Docker $(docker --version | awk '{print $3}') — OK"
echo "  Docker Compose $(docker compose version --short) — OK"
if command -v nvidia-smi &>/dev/null; then
    echo "  $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1) — OK"
else
    echo "  Warning: nvidia-smi not found"
fi
REMOTE_CHECK

echo "[3/7] Configuring Docker daemon TCP listener on DGX..."
ssh $SSH_OPTS "$DGX_HOST" "SUDO_PW='$PW' bash -s" <<REMOTE_DOCKER_TCP
set -euo pipefail
DAEMON_FILE=/etc/systemd/system/docker.service.d/docker-override.conf
DESIRED='[Unit]
After=nvidia-gpu-reset.target tailscaled.service
Wants=nvidia-gpu-reset.target tailscaled.service

[Service]
ExecStart=
ExecStart=/usr/bin/dockerd -H unix:///var/run/docker.sock -H tcp://${DGX_IP}:2375 --containerd=/run/containerd/containerd.sock'
if [[ -f "\$DAEMON_FILE" ]] && [[ "\$(cat "\$DAEMON_FILE")" == "\$DESIRED" ]]; then
    echo "  Already configured"
else
    echo "\$SUDO_PW" | sudo -S -p '' -v
    sudo mkdir -p "\$(dirname "\$DAEMON_FILE")"
    echo "\$DESIRED" | sudo tee "\$DAEMON_FILE" > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl restart docker
    echo "  Reconfigured — daemon restarted"
fi
REMOTE_DOCKER_TCP

echo "[4/7] Syncing files to DGX..."
ssh $SSH_OPTS "$DGX_HOST" "mkdir -p ${DGX_DIR}"
rsync -az -e "ssh $SSH_OPTS" --exclude='.env' --exclude='__pycache__' --exclude='.git' \
    "$REPO_ROOT/" "${DGX_HOST}:${DGX_DIR}/"
scp $SSH_OPTS -q "$REPO_ROOT/.env" "${DGX_HOST}:${DGX_DIR}/.env"
echo "  Files synced (including .env)"

echo "[5/7] Logging into ECR on DGX..."
ECR_PASSWORD=$(aws ecr get-login-password --region us-east-1)
echo "$ECR_PASSWORD" | ssh $SSH_OPTS "$DGX_HOST" "docker login --username AWS --password-stdin 517906913330.dkr.ecr.us-east-1.amazonaws.com"
echo "  ECR login OK"

echo "[6/7] Starting services on DGX..."
ssh $SSH_OPTS "$DGX_HOST" bash -s <<REMOTE_UP
set -euo pipefail
cd "${DGX_DIR}"
# Stop any existing stack (may be from a previous project name)
docker compose --profile monitoring down 2>/dev/null || true
# Also clean up containers from the old 'robolab-workspace' project if present
docker rm -f robolab-redis robolab-ray-head robolab-postgres robolab-minio robolab-minio-init robolab-mlflow robolab-node-exporter robolab-prometheus robolab-grafana 2>/dev/null || true
docker compose --profile monitoring pull
docker compose --profile monitoring up -d
REMOTE_UP

echo "[7/7] Waiting for Ray Dashboard..."
MAX_WAIT=120
ELAPSED=0
while ! curl -sf "http://${DGX_IP}:8265" &>/dev/null; do
    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
        echo "  Warning: Ray Dashboard not reachable after ${MAX_WAIT}s"
        echo "  Debug:   ssh ${DGX_HOST} 'cd ${DGX_DIR} && docker compose logs ray-head'"
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
    echo "  Waiting... (${ELAPSED}s)"
done

if curl -sf "http://${DGX_IP}:8265" &>/dev/null; then
    echo "  Ray Dashboard is ready!"
fi

echo
echo "Setup Complete!"
echo
echo "  Ray Dashboard       http://${DGX_IP}:8265"
echo "  MLflow UI           http://${DGX_IP}:5000"
echo "  Grafana             http://${DGX_IP}:3000"
echo "  MinIO Console       http://${DGX_IP}:9001"
echo "  Prometheus          http://${DGX_IP}:9090"
echo
