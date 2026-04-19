#!/usr/bin/env bash
set -euo pipefail

# DGX seed — run FROM YOUR MAC, once per DGX lifetime.
# Installs k3s, drops the Argo CD bootstrap manifest, and merges DGX's
# kubeconfig into ~/.kube/config as context "dgx".

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
BOOTSTRAP_FILE="$REPO_ROOT/k8s/argocd.yaml"
ENV_FILE="$SCRIPT_DIR/../.env"
KUBECONFIG_FILE="$HOME/.kube/config"

[[ -f "$BOOTSTRAP_FILE" ]] || { echo "Missing $BOOTSTRAP_FILE"; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE"; exit 1; }

set -a; source "$ENV_FILE"; set +a

DGX_IP="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('DGX_TAILSCALE_IP'))")"
DGX_HOST="${DGX_SSH_USER:-$(whoami)}@${DGX_IP}"
BOOTSTRAP_B64="$(base64 < "$BOOTSTRAP_FILE" | tr -d '\n')"

read -s -p "DGX password (SSH + sudo): " PW
echo

SSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new"
export SSHPASS="$PW"

echo "[1/6] Installing k3s + staging Argo bootstrap on DGX..."
$SSH "$DGX_HOST" "SUDO_PW='$PW' BOOTSTRAP_B64='$BOOTSTRAP_B64' bash -s" <<'REMOTE'
set -euo pipefail
echo "$SUDO_PW" | sudo -S -v -p ''
sudo mkdir -p /var/lib/rancher/k3s/server/manifests
echo "$BOOTSTRAP_B64" | base64 -d | sudo tee /var/lib/rancher/k3s/server/manifests/argocd.yaml > /dev/null
curl -sfL https://get.k3s.io | sudo sh -
REMOTE

echo "[2/6] Merging DGX kubeconfig into $KUBECONFIG_FILE as context 'dgx'..."
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

echo "[3/6] Waiting for Argo server to come up..."
until kubectl -n argocd get deploy argocd-server &>/dev/null; do sleep 5; done
kubectl -n argocd rollout status deploy/argocd-server --timeout=5m

echo "[4/6] Publishing .env entries to AWS Secrets Manager at robolab/argocd/*..."
ENV_FILE="$ENV_FILE" uv run python - <<'PYEOF'
import os, boto3
client = boto3.client('secretsmanager', region_name='us-east-1')
with open(os.environ['ENV_FILE']) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        v = v.strip().strip('"').strip("'")
        key = f'robolab/argocd/{k.strip()}'
        try:
            client.put_secret_value(SecretId=key, SecretString=v)
        except client.exceptions.ResourceNotFoundException:
            client.create_secret(Name=key, SecretString=v)
        print(f'  published {key}')
PYEOF

echo "[5/6] Bootstrap: GitHub repo credentials (one-time, ESO will take over)..."
kubectl -n argocd create secret generic argo-github-repo \
  --from-literal=type=git \
  --from-literal=url=https://github.com/paksas/robolab-infra.git \
  --from-literal=username=x-access-token \
  --from-literal=password="$GH_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl label secret argo-github-repo -n argocd argocd.argoproj.io/secret-type=repository --overwrite

echo "[6/6] Seeding AWS credentials for External Secrets Operator..."
kubectl create namespace external-secrets --dry-run=client -o yaml | kubectl apply -f -
kubectl -n external-secrets create secret generic aws-creds \
  --from-literal=AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  --from-literal=AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

echo
echo "Seeded."
echo "  Argo UI:     http://${DGX_IP}:30080  (auth disabled)"
echo "  Context:     dgx (now active — 'kubectl config use-context <other>' to switch)"
