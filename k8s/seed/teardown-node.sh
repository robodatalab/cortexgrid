#!/usr/bin/env bash
set -euo pipefail

# Node teardown — run FROM YOUR MAC.
#
# Usage: teardown-node.sh <tailscale-ip> [ssh-user]
#
# Wipes everything setup-node.sh installed:
#   - k3s (control plane or worker — detected automatically)
#   - nvidia-container-toolkit and its apt repo
# Removes the node from the cluster. If the node was the control plane, also
# scrubs the local kubeconfig context.

(( $# >= 1 )) || { echo "Usage: $0 <tailscale-ip> [ssh-user]"; exit 1; }
NODE_IP="$1"
NODE_USER="${2:-$(whoami)}"
NODE_HOST="${NODE_USER}@${NODE_IP}"

read -rp "This will wipe k3s and nvidia-container-toolkit on ${NODE_IP}. Continue? [y/N] " confirm
[[ "$confirm" == "y" ]] || exit 0

read -s -p "Node password (SSH + sudo): " PW
echo
export SSHPASS="$PW"
SSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new"

NODE_NAME="$(kubectl get nodes -o json 2>/dev/null | jq -r --arg ip "$NODE_IP" '.items[] | select(.status.addresses[].address==$ip) | .metadata.name' | head -1 || true)"

$SSH "$NODE_HOST" "SUDO_PW='$PW' bash -s" <<'REMOTE'
set -euo pipefail
run_sudo() { echo "$SUDO_PW" | sudo -S "$@"; }

if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
  run_sudo /usr/local/bin/k3s-uninstall.sh
  IS_CONTROL_PLANE=1
elif [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
  run_sudo /usr/local/bin/k3s-agent-uninstall.sh
fi

if dpkg -l nvidia-container-toolkit &>/dev/null; then
  run_sudo apt-get remove --purge -y nvidia-container-toolkit
  run_sudo apt-get autoremove -y
fi

run_sudo rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list
run_sudo rm -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
run_sudo apt-get update || true

run_sudo rm -rf /etc/rancher/k3s /var/lib/rancher/k3s /var/lib/kubelet /etc/cni /var/lib/cni
REMOTE

if [[ -n "$NODE_NAME" ]]; then
  kubectl delete node "$NODE_NAME" 2>/dev/null || true
fi

# If this was the control plane, the cluster is gone; also scrub the local kubeconfig
if ! kubectl cluster-info &>/dev/null; then
  kubectl config delete-context dgx 2>/dev/null || true
  kubectl config delete-cluster dgx 2>/dev/null || true
  kubectl config delete-user dgx 2>/dev/null || true
fi

echo "Node teardown complete."
