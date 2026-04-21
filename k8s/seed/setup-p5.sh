#!/usr/bin/env bash
set -euo pipefail

# P5 agent seed — run FROM YOUR MAC, once per P5 lifetime.
# Joins the ThinkStation P5 as a k3s agent to the DGX control plane
# and labels it tier=dev (reserved for PR dev environments).
# Reads the k3s join token from AWS Secrets Manager — no DGX access required.

DGX_IP="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('DGX_TAILSCALE_IP'))")"
P5_IP="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('P5_TAILSCALE_IP'))")"
K3S_TOKEN="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('K3S_NODE_TOKEN'))")"
P5_HOST="${P5_SSH_USER:-ptrochim}@${P5_IP}"

[[ -n "$K3S_TOKEN" ]] || { echo "K3S_NODE_TOKEN not in AWS SM — re-run setup-dgx.sh to publish it"; exit 1; }

read -s -p "P5 password (SSH + sudo): " P5_PW
echo

SSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new"
export SSHPASS="$P5_PW"

echo "[1/2] Installing k3s agent on P5..."
$SSH "$P5_HOST" "SUDO_PW='$P5_PW' K3S_URL='https://${DGX_IP}:6443' K3S_TOKEN='$K3S_TOKEN' bash -s" <<'REMOTE'
set -euo pipefail
echo "$SUDO_PW" | sudo -S -v -p ''
curl -sfL https://get.k3s.io | sudo -E K3S_URL="$K3S_URL" K3S_TOKEN="$K3S_TOKEN" sh -
REMOTE

echo "[2/2] Labelling P5 node as tier=dev..."
for _ in {1..30}; do
  P5_NODE="$(kubectl get nodes -o json | jq -r --arg ip "$P5_IP" '.items[] | select(.status.addresses[].address==$ip) | .metadata.name' | head -1)"
  [[ -n "$P5_NODE" ]] && break
  sleep 2
done
[[ -n "$P5_NODE" ]] || { echo "P5 node never registered with the API server"; exit 1; }
kubectl label node "$P5_NODE" tier=dev --overwrite

echo
echo "P5 joined the cluster."
echo "  Node:    $P5_NODE (tier=dev)"
echo "  Verify:  kubectl get nodes -L tier"
