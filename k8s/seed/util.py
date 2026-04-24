"""Shared helpers for setup-node.py and teardown-node.py.

Holds everything both scripts touch: paths, constants, infra-config.yaml I/O,
.env loading, Fabric SSH helpers, and kubectl wrappers.
"""

import io
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml  # type: ignore
from fabric import Connection  # type: ignore
from paramiko import AutoAddPolicy, SSHConfig  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / "infra-config.yaml"
ENV_FILE = REPO_ROOT / ".env"
KUBE_CONTEXT = "robolab"

# cortexflow.secrets prefixes these with "robolab/infra/".
SECRET_K3S_TOKEN = "K3S_NODE_TOKEN"
SECRET_CONTROL_PLANE_IP = "CONTROL_PLANE_TAILSCALE_IP"

JOIN_SCRIPT_PATH = "/usr/local/bin/robolab-join.sh"
JOIN_ENV_PATH = "/etc/default/robolab-bootstrap"
JOIN_SERVICE_PATH = "/etc/systemd/system/robolab-join.service"
JOIN_TIMER_PATH = "/etc/systemd/system/robolab-join.timer"


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {"nodes": []}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {"nodes": []}


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)


def ssh_user_for_ip(ip: str) -> str | None:
    """Look up the SSH user for `ip` in ~/.ssh/config by matching HostName entries."""
    path = Path.home() / ".ssh" / "config"
    if not path.exists():
        return None
    cfg = SSHConfig.from_path(str(path))
    for host in cfg.get_hostnames():
        if host == "*":
            continue
        opts = cfg.lookup(host)
        if opts.get("hostname") == ip and opts.get("user"):
            return opts["user"]
    return None


def connect(user: str, ip: str, password: str) -> Connection:
    """Open a Fabric SSH connection. Paramiko tries SSH agent + key files first; if
    none authenticate, falls back to the supplied password. Same password is reused
    for sudo on the remote."""
    c = Connection(
        host=ip,
        user=user,
        connect_kwargs={"password": password},
    )
    c.config.sudo.password = password
    c.client.set_missing_host_key_policy(AutoAddPolicy())
    return c


def sudo_script(c: Connection, script: str, hide: bool | str = False) -> str:
    """Upload `script` to a temp file on the remote, run it as root, return stdout.

    Uploading via SFTP first avoids fabric's stdin race — piping a script through
    `in_stream` while `sudo -S` is reading stdin for the password leads to the
    script's first line being consumed as a password attempt.
    """
    tmp = f"/tmp/robolab-seed-{uuid.uuid4().hex}.sh"
    c.put(io.BytesIO(script.encode()), remote=tmp)
    try:
        result = c.sudo(f"bash {tmp}", hide=hide, warn=False)
        return result.stdout
    finally:
        c.sudo(f"rm -f {tmp}", hide=True, warn=True)


def write_remote_file(
    c: Connection, content: str, path: str, mode: str | None = None
) -> None:
    """Write `content` to `path` on the remote as root; optionally chmod.

    SFTP-upload to /tmp as the SSH user, then `sudo mv` into place. Avoids the
    same stdin race that sudo_script avoids.
    """
    tmp = f"/tmp/robolab-file-{uuid.uuid4().hex}"
    c.put(io.BytesIO(content.encode()), remote=tmp)
    c.sudo(f"mv {tmp} {path}", hide=True)
    if mode:
        c.sudo(f"chmod {mode} {path}", hide=True)


def resolve_node_name(node_ip: str, wait_for: float = 0.0) -> str | None:
    """Return the k8s node name whose status.addresses contains `node_ip`.

    With `wait_for > 0`, poll the API server for that many seconds before giving up.
    Returns None if the node is not found within the window.
    """
    deadline = time.monotonic() + wait_for
    while True:
        result = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "json"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for item in data["items"]:
                for addr in item["status"]["addresses"]:
                    if addr["address"] == node_ip:
                        return item["metadata"]["name"]
        if time.monotonic() >= deadline:
            return None
        time.sleep(2)


def kubectl(
    *args: str, check: bool = True, input: str | None = None, capture: bool = True
) -> str:
    result = subprocess.run(
        ["kubectl", *args], input=input, capture_output=capture, text=True,
    )
    if check and result.returncode != 0:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        result.check_returncode()
    return result.stdout if capture else ""
