#!/usr/bin/env python3
"""Worker deferred-join: poll AWS SM for head creds, install k3s-agent, self-remove.

Uploaded to /usr/local/bin/robolab-join.py by JoinCluster._deferred_join and
run periodically by the robolab-join.timer systemd unit. Exits 0 (no-op) until
both head creds appear in AWS SM; then runs the k3s install command, disables
the timer, and removes its own files.

Runtime deps (installed on the worker by JoinCluster._deferred_join):
  - python3 (pre-installed on Ubuntu)
  - python3-boto3 (apt)

SM creds are read from /etc/default/robolab-bootstrap (mode 0600), which
JoinCluster._deferred_join writes at setup time.
"""

import os
import subprocess
import sys
from pathlib import Path

import boto3  # type: ignore
from botocore.exceptions import ClientError  # type: ignore


ENV_PATH = "/etc/default/robolab-bootstrap"
SCRIPT_PATH = "/usr/local/bin/robolab-join.py"
SERVICE_PATH = "/etc/systemd/system/robolab-join.service"
TIMER_PATH = "/etc/systemd/system/robolab-join.timer"

K3S_TOKEN_SECRET = "robolab/infra/K3S_NODE_TOKEN"
CONTROL_PLANE_IP_SECRET = "robolab/infra/CONTROL_PLANE_TAILSCALE_IP"


def _load_env() -> bool:
    if not Path(ENV_PATH).exists():
        return False
    for line in Path(ENV_PATH).read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"')
    return True


def _sm_client():
    return boto3.client(
        "secretsmanager",
        aws_access_key_id=os.environ["SM_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["SM_SECRET_ACCESS_KEY"],
        region_name=os.environ["SM_REGION"],
    )


def _get_secret(client, name: str) -> str | None:
    try:
        return client.get_secret_value(SecretId=name)["SecretString"]
    except ClientError:
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

    client = _sm_client()
    token = _get_secret(client, K3S_TOKEN_SECRET)
    head_ip = _get_secret(client, CONTROL_PLANE_IP_SECRET)
    if not token or not head_ip:
        sys.exit(0)

    subprocess.run(
        f"curl -sfL https://get.k3s.io | "
        f"K3S_URL='https://{head_ip}:6443' K3S_TOKEN='{token}' sh -",
        shell=True,
        check=True,
    )

    _self_remove()
    print("Joined robolab cluster.")


if __name__ == "__main__":
    main()
