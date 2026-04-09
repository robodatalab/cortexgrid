#!/usr/bin/env bash
set -euo pipefail

# Mac Workstation — One-time setup script
#
# Flow:
#   1. Checks AWS CLI can reach Secrets Manager
#   2. If secrets don't exist yet, prompts to create .env and pushes them
#   3. Runs health check

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

echo "[1/3] Checking prerequisites..."

if ! command -v aws &>/dev/null; then
    echo "Error: AWS CLI is not installed."
    echo "  Install: brew install awscli && aws configure"
    exit 1
fi
echo "  aws CLI — OK"

REGION="${AWS_REGION:-us-east-1}"

echo "[2/3] Checking secrets..."

if aws secretsmanager get-secret-value \
    --secret-id "robolab/infra/dgx-tailscale-ip" \
    --region "$REGION" \
    --query 'SecretString' --output text &>/dev/null; then
    echo "  Secrets found in AWS Secrets Manager — OK"
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

DGX_IP=$(aws secretsmanager get-secret-value \
    --secret-id "robolab/infra/dgx-tailscale-ip" \
    --region "$REGION" \
    --query 'SecretString' --output text)

echo "[3/3] Running health check..."
echo
export DGX_TAILSCALE_IP="$DGX_IP"
bash "$REPO_ROOT/scripts/health-check.sh" || true

echo
echo "=========================================="
echo "  Mac Setup Complete!"
echo "=========================================="
echo
echo "  cortexflow.init() will pull secrets from AWS SM automatically."
echo "  No env vars needed — just 'aws configure' once."
echo
echo "  To use cortexflow in a project, add to pyproject.toml:"
echo ""
echo "    [project.dependencies]"
echo "    cortexflow"
echo ""
echo "    [tool.uv.sources]"
echo "    cortexflow = { git = \"https://github.com/paksas/robolab-infra.git\", subdirectory = \"terraform/platform/local-dgx-training\" }"
echo
