#!/usr/bin/env bash
set -euo pipefail

# Mac Workstation — One-time setup script
#
# Flow:
#   1. Checks AWS CLI
#   2. If .env exists but secrets aren't in AWS yet → pushes them
#   3. If secrets are in AWS → pulls them into .env
#   4. Configures shell (RAY_ADDRESS, MLFLOW_TRACKING_URI)
#   5. Runs health check

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
    echo "Error: This script must be run from the local-dgx-training directory."
    echo "  cd terraform/platform/local-dgx-training && scripts/setup-mac.sh"
    exit 1
fi

cd "$REPO_ROOT"

echo "=========================================="
echo "  RoboLab ML Infrastructure — Mac Setup"
echo "=========================================="
echo

echo "[1/4] Checking prerequisites..."

if ! command -v aws &>/dev/null; then
    echo "Error: AWS CLI is not installed."
    echo "  Install: brew install awscli && aws configure"
    exit 1
fi
echo "  aws CLI — OK"

echo "[2/4] Configuring secrets..."
REGION="${AWS_REGION:-us-east-1}"

SECRETS_IN_AWS=false
if aws secretsmanager get-secret-value \
    --secret-id "robolab/infra/dgx-tailscale-ip" \
    --region "$REGION" \
    --query 'SecretString' --output text &>/dev/null; then
    SECRETS_IN_AWS=true
fi

if [[ "$SECRETS_IN_AWS" == "true" ]]; then
    echo "  Secrets found in AWS Secrets Manager — pulling..."
    bash "$REPO_ROOT/scripts/pull-secrets.sh"
else
    echo "  No secrets in AWS Secrets Manager yet — first-time setup."
    if [[ ! -f .env ]]; then
        cp .env.example .env
        echo ""
        echo "  Created .env from template. Please fill in your values:"
        echo "    \$EDITOR .env"
        echo ""
        echo "  Required fields:"
        echo "    DGX_TAILSCALE_IP       (run 'tailscale ip -4' on the DGX)"
        echo "    POSTGRES_PASSWORD      (generate: openssl rand -base64 24)"
        echo "    MINIO_ROOT_PASSWORD    (generate: openssl rand -base64 24)"
        echo "    GRAFANA_ADMIN_PASSWORD (generate: openssl rand -base64 24)"
        echo ""
        read -rp "  Press Enter after editing .env to continue..." _
    fi

    echo "  Pushing secrets to AWS Secrets Manager..."
    bash "$REPO_ROOT/scripts/push-secrets.sh"
fi

set -a
source .env
set +a
DGX_IP="${DGX_TAILSCALE_IP}"

echo "[3/4] Configuring shell environment..."

SHELL_RC="$HOME/.zshrc"
if [[ "$SHELL" == */bash ]]; then
    SHELL_RC="$HOME/.bashrc"
fi

add_export() {
    local var_name="$1"
    local var_value="$2"
    if grep -q "^export ${var_name}=" "$SHELL_RC" 2>/dev/null; then
        sed -i '' "s|^export ${var_name}=.*|export ${var_name}=${var_value}|" "$SHELL_RC"
    else
        echo "export ${var_name}=${var_value}" >> "$SHELL_RC"
    fi
}

add_export "RAY_ADDRESS" "http://${DGX_IP}:8265"
add_export "MLFLOW_TRACKING_URI" "http://${DGX_IP}:5000"
add_export "DGX_TAILSCALE_IP" "${DGX_IP}"

echo "  Updated $SHELL_RC"

export RAY_ADDRESS="http://${DGX_IP}:8265"
export MLFLOW_TRACKING_URI="http://${DGX_IP}:5000"
export DGX_TAILSCALE_IP="${DGX_IP}"

echo "[4/4] Running health check..."
echo
bash "$REPO_ROOT/scripts/health-check.sh" || true

echo
echo "=========================================="
echo "  Mac Setup Complete!"
echo "=========================================="
echo
echo "  Run 'source $SHELL_RC' or open a new terminal to apply changes."
echo
echo "  To use cortexflow in a project, add to pyproject.toml:"
echo ""
echo "    [project.dependencies]"
echo "    cortexflow"
echo ""
echo "    [tool.uv.sources]"
echo "    cortexflow = { git = \"https://github.com/paksas/robolab-infra.git\", subdirectory = \"terraform/platform/local-dgx-training\" }"
echo
