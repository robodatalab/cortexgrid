#!/usr/bin/env bash
set -euo pipefail

# DGX teardown — run FROM YOUR MAC.
# Uninstalls k3s, which tears down Argo and every workload it managed.

DGX_IP="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('DGX_TAILSCALE_IP'))")"
DGX_HOST="${DGX_SSH_USER:-$(whoami)}@${DGX_IP}"

read -rp "This will wipe k3s and ALL cluster state on the DGX. Continue? [y/N] " confirm
[[ "$confirm" == "y" ]] || exit 0

read -s -p "DGX password (SSH + sudo): " PW
echo
export SSHPASS="$PW"

sshpass -e ssh -o StrictHostKeyChecking=accept-new "$DGX_HOST" \
  "echo '$PW' | sudo -S /usr/local/bin/k3s-uninstall.sh"

echo "DGX teardown complete."
