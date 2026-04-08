#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Add an AWS EC2 node to the Ray cluster
# =============================================================================
# Prints the command to run on a new EC2 instance to join the Ray cluster.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
    echo "Error: This script must be run from the local-dgx-training directory."
    echo "  cd terraform/platform/local-dgx-training && scripts/add-aws-node.sh"
    exit 1
fi

# Load .env
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

DGX_IP="${DGX_TAILSCALE_IP:-}"
if [[ -z "$DGX_IP" || "$DGX_IP" == "100.x.x.x" ]]; then
    echo "Error: DGX_TAILSCALE_IP is not set in .env"
    exit 1
fi

NUM_GPUS="${1:-1}"

echo "=========================================="
echo "  Add AWS EC2 Node to Ray Cluster"
echo "=========================================="
echo
echo "Prerequisites on the EC2 instance:"
echo "  1. Install Tailscale and join your network"
echo "  2. Install NVIDIA drivers"
echo "  3. Install Docker + NVIDIA Container Toolkit"
echo "  4. Install Ray: pip install 'ray[default]==2.9.3'"
echo
echo "Then run this command on the EC2 instance:"
echo
echo "  ray start --address=${DGX_IP}:6379 --num-gpus=${NUM_GPUS} --block"
echo
echo "Or with Docker:"
echo
echo "  docker run -d --gpus all --network host \\"
echo "    --name ray-worker \\"
echo "    --restart unless-stopped \\"
echo "    <your-ray-image> \\"
echo "    ray start --address=${DGX_IP}:6379 --num-gpus=${NUM_GPUS} --block"
echo
echo "After joining, the node will appear in the Ray Dashboard:"
echo "  http://${DGX_IP}:8265"
echo
