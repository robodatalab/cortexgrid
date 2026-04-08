#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Health Check — verify all services are reachable
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env if available
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

DGX_IP="${DGX_TAILSCALE_IP:-localhost}"

check_service() {
    local name="$1"
    local url="$2"
    local timeout="${3:-5}"

    if curl -sf --max-time "$timeout" "$url" &>/dev/null; then
        echo "  ✅  ${name}: ${url}"
        return 0
    else
        echo "  ❌  ${name}: ${url}"
        return 1
    fi
}

echo "Service Health Check"
echo "────────────────────────────────────────"
echo

FAILURES=0

check_service "Ray Dashboard" "http://${DGX_IP}:8265" || FAILURES=$((FAILURES + 1))
check_service "MLflow"        "http://${DGX_IP}:5000" || FAILURES=$((FAILURES + 1))
check_service "MinIO"         "http://${DGX_IP}:9000/minio/health/live" || FAILURES=$((FAILURES + 1))
check_service "Grafana"       "http://${DGX_IP}:3000" || FAILURES=$((FAILURES + 1))
check_service "Prometheus"    "http://${DGX_IP}:9090/-/healthy" || FAILURES=$((FAILURES + 1))

echo
if [[ $FAILURES -eq 0 ]]; then
    echo "All services are healthy."
else
    echo "${FAILURES} service(s) unreachable."
    echo "  Check if the DGX stack is running: docker compose ps"
    echo "  Check if Tailscale is connected:   tailscale status"
    exit 1
fi
