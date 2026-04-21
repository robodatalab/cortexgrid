#!/usr/bin/env bash
set -euo pipefail

# Node seed — run FROM YOUR MAC, once per node lifetime.
#
# Usage: setup-node.sh <tailscale-ip> <tier> [ssh-user]
#   tier: "main" (hosts main branch) or "dev" (hosts dev branch workloads)
#
# The first node seeded becomes the k3s control plane and bootstraps Argo CD.
# Every subsequent node joins as a worker. tier is independent — labels the
# node for branch-scoped scheduling, not for k3s control-plane placement.

(( $# >= 2 )) || { echo "Usage: $0 <tailscale-ip> <tier:main|dev> [ssh-user]"; exit 1; }
NODE_IP="$1"
TIER="$2"
NODE_USER="${3:-$(whoami)}"
NODE_HOST="${NODE_USER}@${NODE_IP}"

[[ "$TIER" == "main" || "$TIER" == "dev" ]] || { echo "tier must be 'main' or 'dev'"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BOOTSTRAP_FILE="$REPO_ROOT/k8s/argocd.yaml"
ENV_FILE="$SCRIPT_DIR/.env"
KUBECONFIG_FILE="$HOME/.kube/config"

# --- Determine whether a cluster already exists ------------------------------
K3S_TOKEN="$(uv run python -c "
from cortexflow.secrets import get_secret
try: print(get_secret('K3S_NODE_TOKEN'))
except Exception: pass
")"

if [[ -z "$K3S_TOKEN" ]]; then
  ROLE="control-plane"
  [[ -f "$BOOTSTRAP_FILE" ]] || { echo "Missing $BOOTSTRAP_FILE"; exit 1; }
  [[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE"; exit 1; }
  set -a; source "$ENV_FILE"; set +a
  BOOTSTRAP_B64="$(base64 < "$BOOTSTRAP_FILE" | tr -d '\n')"
else
  ROLE="worker"
  CONTROL_PLANE_IP="$(uv run python -c "from cortexflow.secrets import get_secret; print(get_secret('DGX_TAILSCALE_IP'))")"
fi

echo "Role: $ROLE | tier: $TIER | node: $NODE_HOST"

read -s -p "Node password (SSH + sudo): " PW
echo
SSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new"
export SSHPASS="$PW"

# --- Node prerequisites (nvidia-container-toolkit) ---------------------------
echo "Installing prerequisites on node..."
$SSH "$NODE_HOST" "SUDO_PW='$PW' bash -s" <<'REMOTE'
set -euo pipefail
run_sudo() { echo "$SUDO_PW" | sudo -S "$@"; }

if ! command -v nvidia-ctk &>/dev/null; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | run_sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | run_sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
  run_sudo apt-get update
  run_sudo apt-get install -y nvidia-container-toolkit
fi
REMOTE

# --- Install k3s -------------------------------------------------------------
if [[ "$ROLE" == "control-plane" ]]; then
  echo "Installing k3s control plane + staging Argo bootstrap..."
  $SSH "$NODE_HOST" "SUDO_PW='$PW' BOOTSTRAP_B64='$BOOTSTRAP_B64' bash -s" <<'REMOTE'
set -euo pipefail
echo "$SUDO_PW" | sudo -S -v -p ''
sudo mkdir -p /var/lib/rancher/k3s/server/manifests
echo "$BOOTSTRAP_B64" | base64 -d | sudo tee /var/lib/rancher/k3s/server/manifests/argocd.yaml > /dev/null
curl -sfL https://get.k3s.io | sudo sh -
REMOTE
else
  echo "Installing k3s worker..."
  $SSH "$NODE_HOST" "SUDO_PW='$PW' K3S_URL='https://${CONTROL_PLANE_IP}:6443' K3S_TOKEN='$K3S_TOKEN' bash -s" <<'REMOTE'
set -euo pipefail
echo "$SUDO_PW" | sudo -S -v -p ''
curl -sfL https://get.k3s.io | sudo -E K3S_URL="$K3S_URL" K3S_TOKEN="$K3S_TOKEN" sh -
REMOTE
fi

# --- Control-plane-only bootstrap --------------------------------------------
if [[ "$ROLE" == "control-plane" ]]; then
  echo "Merging kubeconfig into $KUBECONFIG_FILE as context 'dgx'..."
  mkdir -p "$(dirname "$KUBECONFIG_FILE")"
  [[ -f "$KUBECONFIG_FILE" ]] || touch "$KUBECONFIG_FILE"
  TMP_KCONFIG="$(mktemp)"
  trap 'rm -f "$TMP_KCONFIG"' EXIT
  $SSH "$NODE_HOST" "echo '$PW' | sudo -S cat /etc/rancher/k3s/k3s.yaml" \
    | sed -e "s|127.0.0.1|$NODE_IP|" \
          -e "s|name: default$|name: dgx|" \
          -e "s|: default$|: dgx|" \
          -e "s|current-context: default|current-context: dgx|" > "$TMP_KCONFIG"
  KUBECONFIG="$KUBECONFIG_FILE:$TMP_KCONFIG" kubectl config view --flatten > "${KUBECONFIG_FILE}.new"
  mv "${KUBECONFIG_FILE}.new" "$KUBECONFIG_FILE"
  chmod 600 "$KUBECONFIG_FILE"
  kubectl config use-context dgx > /dev/null

  echo "Waiting for Argo server to come up..."
  until kubectl -n argocd get deploy argocd-server &>/dev/null; do sleep 5; done
  kubectl -n argocd rollout status deploy/argocd-server --timeout=5m

  echo "Publishing .env entries to AWS Secrets Manager at robolab/argocd/*..."
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

  echo "Bootstrap: GitHub repo credentials..."
  kubectl -n argocd create secret generic argo-github-repo \
    --from-literal=type=git \
    --from-literal=url=https://github.com/paksas/robolab-infra.git \
    --from-literal=username=x-access-token \
    --from-literal=password="$GH_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl label secret argo-github-repo -n argocd argocd.argoproj.io/secret-type=repository --overwrite

  echo "Seeding AWS bootstrap credentials for ESO..."
  kubectl create namespace external-secrets --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: aws-bootstrap-creds
  namespace: external-secrets
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "${AWS_ACCESS_KEY_ID}"
  AWS_SECRET_ACCESS_KEY: "${AWS_SECRET_ACCESS_KEY}"
EOF

  echo "Publishing k3s token + control-plane IP to AWS SM..."
  NODE_TOKEN="$($SSH "$NODE_HOST" "echo '$PW' | sudo -S cat /var/lib/rancher/k3s/server/node-token" | tr -d '[:space:]')"
  uv run python - <<PYEOF
import boto3
client = boto3.client('secretsmanager', region_name='us-east-1')
for key, val in [('robolab/infra/K3S_NODE_TOKEN', "${NODE_TOKEN}"),
                 ('robolab/infra/DGX_TAILSCALE_IP', "${NODE_IP}")]:
    try:
        client.put_secret_value(SecretId=key, SecretString=val)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=key, SecretString=val)
PYEOF
fi

# --- Label the node ----------------------------------------------------------
echo "Labelling node tier=${TIER}..."
for _ in {1..30}; do
  NODE_NAME="$(kubectl get nodes -o json | jq -r --arg ip "$NODE_IP" '.items[] | select(.status.addresses[].address==$ip) | .metadata.name' | head -1)"
  [[ -n "$NODE_NAME" ]] && break
  sleep 2
done
[[ -n "$NODE_NAME" ]] || { echo "Node never registered with API server"; exit 1; }
kubectl label node "$NODE_NAME" tier="$TIER" --overwrite

echo
echo "Seeded."
echo "  Node:  $NODE_NAME (role=$ROLE, tier=$TIER)"
if [[ "$ROLE" == "control-plane" ]]; then
  echo "  Argo UI: http://${NODE_IP}:30080  (auth disabled, via Tailscale)"
fi
