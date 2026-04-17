#!/usr/bin/env bash
set -euo pipefail

# Verify Mac prerequisites for running `make setup-dgx`:
#   1. `uv` is installed (used to invoke cortexflow.secrets)
#   2. The CMS is reachable with the current credentials
#
# Config values themselves are managed via the Platform UI — not
# through this script.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/docker-compose.yml" ]]; then
    echo "Error: Run from the local-dgx-training directory."
    exit 1
fi

echo "[1/2] Checking prerequisites..."
if ! command -v uv &>/dev/null; then
    echo "Error: uv is not installed."
    echo "  Install: brew install uv"
    exit 1
fi
echo "  uv OK"

echo "[2/2] Checking CMS access..."
if ! uv run python -c "from cortexflow.secrets import list_secrets; list_secrets()" &>/dev/null; then
    echo "Error: Cannot read config from the CMS."
    echo "  Check that your credentials are configured and authorized."
    exit 1
fi
echo "  CMS access OK"

echo
echo "Mac is ready. Next: make setup-dgx"
