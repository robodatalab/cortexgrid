#!/usr/bin/env bash
set -euo pipefail

# Report the docker compose health status of each service.
# Run this from the DGX (or any host where the compose stack is up).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

docker compose --profile monitoring ps \
    --format 'table {{.Service}}\t{{.Status}}\t{{.Health}}'
