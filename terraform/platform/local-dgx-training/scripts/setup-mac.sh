#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Mac Workstation — One-time client setup script
# =============================================================================
#
# First-time flow:
#   1. Checks Python + AWS CLI
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

# --- Step 1: Check prerequisites ---
echo "[1/5] Checking prerequisites..."

# Python
PYTHON_CMD=""
for cmd in python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" --version 2>&1 | awk '{print $2}')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    echo "Error: Python 3.11+ is required."
    echo "  Install: brew install python@3.11"
    exit 1
fi
echo "  $($PYTHON_CMD --version) — OK"

# AWS CLI
if ! command -v aws &>/dev/null; then
    echo "Error: AWS CLI is not installed."
    echo "  Install: brew install awscli && aws configure"
    exit 1
fi
echo "  aws CLI — OK"

# --- Step 2: Install pip packages ---
echo "[2/5] Installing client-side packages..."
$PYTHON_CMD -m pip install --quiet "ray[default]==2.9.3" "mlflow==2.10.2" "boto3==1.34.29" "pyyaml" "python-dotenv"
echo "  Installed ray, mlflow, boto3, pyyaml, python-dotenv"

# --- Step 3: Secrets — push or pull ---
echo "[3/5] Configuring secrets..."
REGION="${AWS_REGION:-us-east-1}"

# Check if secrets already exist in AWS SM
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
    # Ensure .env exists
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

# Load the .env to get DGX_IP
set -a
source .env
set +a
DGX_IP="${DGX_TAILSCALE_IP}"

# --- Step 4: Configure shell ---
echo "[4/5] Configuring shell environment..."

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

# --- Step 5: Health check ---
echo "[5/5] Running health check..."
echo
bash "$REPO_ROOT/scripts/health-check.sh" || true

echo
echo "=========================================="
echo "  Mac Setup Complete!"
echo "=========================================="
echo
echo "  Run 'source $SHELL_RC' or open a new terminal to apply changes."
echo
echo "  Submit a job:    python jobs/submit.py --help"
echo "  Monitor status:  python jobs/monitor.py"
echo
