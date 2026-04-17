#!/usr/bin/env bash
set -euo pipefail

# DGX Spark teardown — run this FROM YOUR MAC.
# SSHs into the DGX and stops all services, removes volumes and .env.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
    echo "Error: Run from the local-dgx-training directory."
    exit 1
fi

cd "$REPO_ROOT"

DGX_IP="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('DGX_TAILSCALE_IP'))")"

DGX_USER="${DGX_SSH_USER:-$(whoami)}"
DGX_HOST="${DGX_USER}@${DGX_IP}"
DGX_DIR="${DGX_REPO_PATH:-/home/${DGX_USER}/robolab-infra/terraform/platform/local-dgx-training}"

CTRL_SOCKET="/tmp/robolab-ssh-${DGX_USER}-${DGX_IP}"
SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o ControlMaster=auto -o ControlPath=${CTRL_SOCKET} -o ControlPersist=120"

cleanup() { ssh -O exit -o ControlPath="${CTRL_SOCKET}" "$DGX_HOST" 2>/dev/null || true; }
trap cleanup EXIT

echo "This will stop all services on the DGX and DELETE all data (volumes)."
read -rp "Are you sure? [y/N] " confirm
[[ "$confirm" == "y" ]] || exit 0

echo "Tearing down DGX at ${DGX_HOST}..."

ssh $SSH_OPTS "$DGX_HOST" bash -s <<REMOTE_DOWN
set -euo pipefail
cd "${DGX_DIR}" 2>/dev/null || { echo "Directory not found on DGX"; exit 0; }
docker compose --profile monitoring down -v
rm -f .env
echo "Done."
REMOTE_DOWN

echo "DGX teardown complete. Volumes and .env removed."
