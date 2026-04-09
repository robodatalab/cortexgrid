#!/usr/bin/env bash
set -euo pipefail

# Push all KEY=VALUE pairs from .env to AWS Secrets Manager.
# Each env var becomes robolab/infra/<KEY>.
# Safe to run multiple times — existing secrets are updated in place.
#
# Usage:
#   bash scripts/push-secrets.sh
#   AWS_PROFILE=robolab bash scripts/push-secrets.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
    echo "Error: Run from the local-dgx-training directory." >&2
    exit 1
fi

ENV_FILE="$REPO_ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: .env not found. Copy .env.example to .env and fill in your values first:" >&2
    echo "  cp .env.example .env" >&2
    exit 1
fi

if ! command -v aws &>/dev/null; then
    echo "Error: aws CLI is not installed." >&2
    echo "  Install: brew install awscli" >&2
    exit 1
fi

# Source .env so variable references (e.g. ${DGX_TAILSCALE_IP}) resolve
set -a
source "$ENV_FILE"
set +a

REGION="${AWS_REGION:-us-east-1}"

put_secret() {
    local secret_name="$1"
    local secret_value="$2"

    if aws secretsmanager describe-secret --secret-id "$secret_name" --region "$REGION" &>/dev/null; then
        aws secretsmanager put-secret-value \
            --secret-id "$secret_name" \
            --secret-string "$secret_value" \
            --region "$REGION" \
            --output text --query 'Name' >/dev/null
        echo "  updated  $secret_name"
    else
        aws secretsmanager create-secret \
            --name "$secret_name" \
            --secret-string "$secret_value" \
            --region "$REGION" \
            --output text --query 'Name' >/dev/null
        echo "  created  $secret_name"
    fi
}

echo "Pushing secrets to AWS Secrets Manager (region: ${REGION})..."
echo

# Parse every KEY=VALUE line from .env, push each as robolab/infra/KEY
while IFS='=' read -r key _; do
    value="${!key}"
    put_secret "robolab/infra/${key}" "$value"
done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE")

echo
echo "Done."
