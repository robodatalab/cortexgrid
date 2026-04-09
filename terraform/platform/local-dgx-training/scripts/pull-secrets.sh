#!/usr/bin/env bash
set -euo pipefail

# Pull all robolab/infra/* secrets from AWS Secrets Manager and write .env.
# Each secret robolab/infra/<KEY> becomes a KEY=VALUE line.
#
# Usage:
#   bash scripts/pull-secrets.sh                # writes .env
#   bash scripts/pull-secrets.sh --print        # prints to stdout (no file write)
#   AWS_PROFILE=robolab bash scripts/pull-secrets.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
    echo "Error: Run from the local-dgx-training directory." >&2
    echo "  cd terraform/platform/local-dgx-training && bash scripts/pull-secrets.sh" >&2
    exit 1
fi

REGION="${AWS_REGION:-us-east-1}"
PRINT_ONLY=false
if [[ "${1:-}" == "--print" ]]; then
    PRINT_ONLY=true
fi

if ! command -v aws &>/dev/null; then
    echo "Error: aws CLI is not installed." >&2
    echo "  Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" >&2
    exit 1
fi

echo "Pulling secrets from AWS Secrets Manager (region: ${REGION})..." >&2

PREFIX="robolab/infra/"

# List all secret names under the prefix
secret_names=$(aws secretsmanager list-secrets \
    --filters Key=name,Values="$PREFIX" \
    --region "$REGION" \
    --query 'SecretList[].Name' \
    --output text)

if [[ -z "$secret_names" ]]; then
    echo "Error: No secrets found under ${PREFIX}" >&2
    exit 1
fi

# Fetch each secret and build KEY=VALUE lines
ENV_CONTENT=""
for secret_name in $secret_names; do
    key="${secret_name#"$PREFIX"}"
    value=$(aws secretsmanager get-secret-value \
        --secret-id "$secret_name" \
        --region "$REGION" \
        --query 'SecretString' \
        --output text 2>/dev/null) || { echo "Error: Failed to fetch $secret_name" >&2; exit 1; }
    ENV_CONTENT+="${key}=${value}"$'\n'
done

echo "All secrets fetched successfully." >&2

if [[ "$PRINT_ONLY" == "true" ]]; then
    printf '%s' "$ENV_CONTENT"
else
    printf '%s' "$ENV_CONTENT" > "$REPO_ROOT/.env"
    echo "Wrote $REPO_ROOT/.env" >&2
fi
