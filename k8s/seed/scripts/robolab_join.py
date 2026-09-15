#!/usr/bin/env python3
"""Worker deferred-join: poll the head secrets server for head creds, install k3s-agent, self-remove.

Uploaded to /usr/local/bin/robolab-join.py by JoinCluster._deferred_join and
run periodically by the robolab-join.timer systemd unit. Exits 0 (no-op) until
both head creds are readable from the head secrets server; then runs the k3s
install command, disables the timer, and removes its own files.

Runtime deps: python3 (pre-installed on Ubuntu); standard library only.

The head server URL and the k3s version to install are read from
/etc/default/robolab-bootstrap (mode 0600), which JoinCluster._deferred_join
writes at setup time.
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


ENV_PATH = "/etc/default/robolab-bootstrap"
SCRIPT_PATH = "/usr/local/bin/robolab-join.py"
SERVICE_PATH = "/etc/systemd/system/robolab-join.service"
TIMER_PATH = "/etc/systemd/system/robolab-join.timer"

K3S_TOKEN_SECRET = "K3S_NODE_TOKEN"
CONTROL_PLANE_IP_SECRET = "CONTROL_PLANE_TAILSCALE_IP"


def _load_env() -> bool:
    if not Path(ENV_PATH).exists():
        return False
    for line in Path(ENV_PATH).read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"')
    return True


def _get_secret(head_url: str, name: str) -> str | None:
    # Unreachable head, missing secret, bad response: all mean "not yet".
    try:
        with urllib.request.urlopen(f"{head_url}/secrets/{name}", timeout=10) as response:
            return json.load(response)["value"]
    except (OSError, ValueError, KeyError):
        return None


def _self_remove() -> None:
    subprocess.run(
        ["systemctl", "disable", "--now", "robolab-join.timer"], check=False
    )
    for path in (ENV_PATH, SCRIPT_PATH, SERVICE_PATH, TIMER_PATH):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    subprocess.run(["systemctl", "daemon-reload"], check=False)


def main() -> None:
    if not _load_env():
        sys.exit(0)

    head_url = os.environ["CORTEXGRID_HEAD_URL"].rstrip("/")
    k3s_version = os.environ["K3S_VERSION"]
    token = _get_secret(head_url, K3S_TOKEN_SECRET)
    head_ip = _get_secret(head_url, CONTROL_PLANE_IP_SECRET)
    if not token or not head_ip:
        sys.exit(0)

    subprocess.run(
        f"curl -sfL https://get.k3s.io | "
        f"INSTALL_K3S_VERSION='{k3s_version}' "
        f"K3S_URL='https://{head_ip}:6443' K3S_TOKEN='{token}' sh -",
        shell=True,
        check=True,
    )

    _self_remove()
    print("Joined robolab cluster.")


if __name__ == "__main__":
    main()
