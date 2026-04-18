#!/usr/bin/env bash
set -euo pipefail

# DGX seed — run FROM YOUR MAC, once per DGX lifetime.
# Installs k3s, drops the Argo CD bootstrap manifest, fetches kubeconfig
# and the Argo admin password back to the Mac. After this, Argo reconciles
# everything from Git.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
BOOTSTRAP_FILE="$REPO_ROOT/k8s/argocd.yaml"
KUBECONFIG_OUT="${KUBECONFIG_OUT:-$HOME/.kube/dgx-config}"

[[ -f "$BOOTSTRAP_FILE" ]] || { echo "Missing $BOOTSTRAP_FILE"; exit 1; }

DGX_IP="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('DGX_TAILSCALE_IP'))")"
DGX_HOST="${DGX_SSH_USER:-$(whoami)}@${DGX_IP}"
BOOTSTRAP_B64="$(base64 < "$BOOTSTRAP_FILE" | tr -d '\n')"

read -s -p "DGX password (SSH + sudo): " PW
echo

SSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new"
export SSHPASS="$PW"

echo "[1/4] Installing k3s + staging Argo bootstrap on DGX..."
$SSH "$DGX_HOST" "SUDO_PW='$PW' BOOTSTRAP_B64='$BOOTSTRAP_B64' bash -s" <<'REMOTE'
set -euo pipefail
echo "$SUDO_PW" | sudo -S -v -p ''
sudo mkdir -p /var/lib/rancher/k3s/server/manifests
echo "$BOOTSTRAP_B64" | base64 -d | sudo tee /var/lib/rancher/k3s/server/manifests/argocd.yaml > /dev/null
curl -sfL https://get.k3s.io | sudo sh -
REMOTE

echo "[2/4] Fetching kubeconfig..."
mkdir -p "$(dirname "$KUBECONFIG_OUT")"
$SSH "$DGX_HOST" "echo '$PW' | sudo -S cat /etc/rancher/k3s/k3s.yaml" \
  | sed "s|127.0.0.1|$DGX_IP|" > "$KUBECONFIG_OUT"
chmod 600 "$KUBECONFIG_OUT"

echo "[3/4] Waiting for Argo server to come up..."
export KUBECONFIG="$KUBECONFIG_OUT"
until kubectl -n argocd get deploy argocd-server &>/dev/null; do sleep 5; done
kubectl -n argocd rollout status deploy/argocd-server --timeout=5m

echo
echo "Seeded."
echo "  Argo UI:     http://${DGX_IP}:30080  (auth disabled)"
echo "  Kubeconfig:  ${KUBECONFIG_OUT}  (export KUBECONFIG=${KUBECONFIG_OUT})"
