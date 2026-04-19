#!/bin/sh
set -eu

# Ray Dashboard uses RAY_GRAFANA_IFRAME_HOST as the src of an iframe
# loaded by the user's browser, so the URL must be externally reachable
# (the Tailscale IP), not an internal docker hostname.

export RAY_GRAFANA_IFRAME_HOST="http://${DGX_TAILSCALE_IP}:3000"

exec ray start --head \
    --node-ip-address=0.0.0.0 \
    --dashboard-host=0.0.0.0 \
    --dashboard-port=8265 \
    --metrics-export-port=8080 \
    --block
