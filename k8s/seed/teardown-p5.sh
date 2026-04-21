#!/usr/bin/env bash
set -euo pipefail

# P5 teardown — run FROM YOUR MAC.
# Uninstalls everything setup-p5.sh put on the P5:
#  - k3s agent + all cluster state
#  - nvidia-container-toolkit + its apt repo
# Also removes the P5 node from the cluster.

P5_IP="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('P5_TAILSCALE_IP'))")"
P5_HOST="${P5_SSH_USER:-ptrochim}@${P5_IP}"

read -rp "This will wipe k3s-agent and nvidia-container-toolkit on P5. Continue? [y/N] " confirm
[[ "$confirm" == "y" ]] || exit 0

read -s -p "P5 password (SSH + sudo): " PW
echo
export SSHPASS="$PW"

P5_NODE="$(kubectl get nodes -o json | jq -r --arg ip "$P5_IP" '.items[] | select(.status.addresses[].address==$ip) | .metadata.name' | head -1)"

sshpass -e ssh -o StrictHostKeyChecking=accept-new "$P5_HOST" "SUDO_PW='$PW' bash -s" <<'REMOTE'
set -euo pipefail
run_sudo() { echo "$SUDO_PW" | sudo -S "$@"; }

if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
  run_sudo /usr/local/bin/k3s-agent-uninstall.sh
fi

if dpkg -l nvidia-container-toolkit &>/dev/null; then
  run_sudo apt-get remove --purge -y nvidia-container-toolkit
  run_sudo apt-get autoremove -y
fi

run_sudo rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list
run_sudo rm -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
run_sudo apt-get update

run_sudo rm -rf /etc/rancher/k3s /var/lib/rancher/k3s /var/lib/kubelet /etc/cni /var/lib/cni
REMOTE

if [[ -n "$P5_NODE" ]]; then
  kubectl delete node "$P5_NODE" 2>/dev/null || true
fi

echo "P5 teardown complete."
