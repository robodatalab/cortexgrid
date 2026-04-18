#!/usr/bin/env bash
set -euo pipefail

# DGX seed — run FROM YOUR MAC, once per DGX lifetime.
# Installs k3s, drops the Argo CD bootstrap manifest, and merges DGX's
# kubeconfig into ~/.kube/config as context "dgx".

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
BOOTSTRAP_FILE="$REPO_ROOT/k8s/argocd.yaml"
KUBECONFIG_FILE="$HOME/.kube/config"

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

echo "[2/4] Merging DGX kubeconfig into $KUBECONFIG_FILE as context 'dgx'..."
mkdir -p "$(dirname "$KUBECONFIG_FILE")"
[[ -f "$KUBECONFIG_FILE" ]] || touch "$KUBECONFIG_FILE"
TMP_KCONFIG="$(mktemp)"
trap 'rm -f "$TMP_KCONFIG"' EXIT

$SSH "$DGX_HOST" "echo '$PW' | sudo -S cat /etc/rancher/k3s/k3s.yaml" \
  | sed -e "s|127.0.0.1|$DGX_IP|" \
        -e "s|name: default$|name: dgx|" \
        -e "s|: default$|: dgx|" \
        -e "s|current-context: default|current-context: dgx|" > "$TMP_KCONFIG"

KUBECONFIG="$KUBECONFIG_FILE:$TMP_KCONFIG" kubectl config view --flatten > "${KUBECONFIG_FILE}.new"
mv "${KUBECONFIG_FILE}.new" "$KUBECONFIG_FILE"
chmod 600 "$KUBECONFIG_FILE"
kubectl config use-context dgx > /dev/null

echo "[3/4] Waiting for Argo server to come up..."
until kubectl -n argocd get deploy argocd-server &>/dev/null; do sleep 5; done
kubectl -n argocd rollout status deploy/argocd-server --timeout=5m

echo
echo "Seeded."
echo "  Argo UI:     http://${DGX_IP}:30080  (auth disabled)"
echo "  Context:     dgx (now active — 'kubectl config use-context <other>' to switch)"
