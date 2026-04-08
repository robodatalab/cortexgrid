#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Push secrets from .env to AWS Secrets Manager
# =============================================================================
# Reads the local .env and creates or updates each secret in AWS SM.
# Safe to run multiple times — existing secrets are updated in place.
#
# Usage:
#   bash scripts/push-secrets.sh              # push from .env
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

# Load .env
set -a
source "$ENV_FILE"
set +a

REGION="${AWS_REGION:-us-east-1}"

# --- Check aws CLI ---
if ! command -v aws &>/dev/null; then
    echo "Error: aws CLI is not installed." >&2
    echo "  Install: brew install awscli" >&2
    exit 1
fi

# --- Helper: create or update a secret ---
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

# --- Validate required values are set ---
missing=()
[[ -z "${DGX_TAILSCALE_IP:-}" || "${DGX_TAILSCALE_IP}" == "100.x.x.x" ]] && missing+=("DGX_TAILSCALE_IP")
[[ -z "${POSTGRES_PASSWORD:-}" ]] && missing+=("POSTGRES_PASSWORD")
[[ -z "${MINIO_ROOT_PASSWORD:-}" ]] && missing+=("MINIO_ROOT_PASSWORD")
[[ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]] && missing+=("GRAFANA_ADMIN_PASSWORD")

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Error: The following required values are missing or placeholder in .env:" >&2
    for var in "${missing[@]}"; do
        echo "  - $var" >&2
    done
    echo "" >&2
    echo "Fill them in: \$EDITOR .env" >&2
    exit 1
fi

echo "Pushing secrets to AWS Secrets Manager (region: ${REGION})..."
echo

put_secret "robolab/infra/dgx-tailscale-ip"       "$DGX_TAILSCALE_IP"
put_secret "robolab/infra/mlflow-postgres-password" "$POSTGRES_PASSWORD"
put_secret "robolab/infra/minio-root-password"      "$MINIO_ROOT_PASSWORD"
put_secret "robolab/infra/grafana-admin-password"   "$GRAFANA_ADMIN_PASSWORD"

echo
echo "Done. All infra secrets are now in AWS Secrets Manager."
