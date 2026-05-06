"""Shared helpers for setup-node.py and teardown-node.py.

Holds everything both scripts touch: paths, constants, infra-config.yaml I/O,
.env loading, Fabric SSH helpers, and kubectl wrappers.
"""

import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import textwrap
import time
import uuid
from typing import Callable

import yaml  # type: ignore
from fabric import Connection  # type: ignore
from paramiko import AuthenticationException, AutoAddPolicy, SSHConfig  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / "infra-config.yaml"
ENV_FILE = REPO_ROOT / ".env"
KUBE_CONTEXT = "robolab"

# cortexflow.secrets prefixes these with "robolab/infra/".
SECRET_K3S_TOKEN = "K3S_NODE_TOKEN"
SECRET_CONTROL_PLANE_IP = "CONTROL_PLANE_TAILSCALE_IP"
SECRET_MLFLOW_TRACKING_URI = "MLFLOW_TRACKING_URI"
SECRET_RAY_JOB_SERVER_URI = "RAY_JOB_SERVER_URI"

# NodePorts must match k8s/workloads/{mlflow,ray}/service.yaml. Seed pipeline
# stores the full URL in SM at setup time; downstream consumers read the URL,
# not the port.
_MLFLOW_NODEPORT = 30500
_RAY_DASHBOARD_NODEPORT = 30265
_POSTGRES_NODEPORT = 30432


def mlflow_tracking_uri_for(tailscale_ip: str) -> str:
    return f"http://{tailscale_ip}:{_MLFLOW_NODEPORT}"


def ray_job_server_uri_for(tailscale_ip: str) -> str:
    return f"http://{tailscale_ip}:{_RAY_DASHBOARD_NODEPORT}"


def postgres_uri_for(tailscale_ip: str, db: str, user: str, password: str) -> str:
    return f"postgresql://{user}:{password}@{tailscale_ip}:{_POSTGRES_NODEPORT}/{db}"

JOIN_SCRIPT_PATH = "/usr/local/bin/robolab-join.py"
JOIN_ENV_PATH = "/etc/default/robolab-bootstrap"
JOIN_SERVICE_PATH = "/etc/systemd/system/robolab-join.service"
JOIN_TIMER_PATH = "/etc/systemd/system/robolab-join.timer"


log = logging.getLogger("k8s.seed.util")


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {"nodes": []}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {"nodes": []}


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)


def checkpoint_step_done(ip: str, operator_name: str, direction: str) -> None:
    """Record progress of a pipeline step against the node entry in infra-config.yaml.

    Setup appends operator_name to node['progress']. Teardown pops the last
    entry iff it matches operator_name (teardown runs operators in reverse,
    so the last-completed setup step is the first to be torn down).
    The `progress` field is removed when the list becomes empty.
    """
    if direction not in ("setup", "teardown"):
        raise ValueError(f"direction must be 'setup' or 'teardown', got {direction!r}")
    cfg = load_config()
    for node in cfg["nodes"]:
        if node["ip"] != ip:
            continue
        progress = node.setdefault("progress", [])
        if direction == "setup":
            progress.append(operator_name)
        elif progress and progress[-1] == operator_name:
            progress.pop()
        if not progress:
            node.pop("progress", None)
        break
    save_config(cfg)


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


def connect(
    user: str,
    ip: str,
    ssh_password: Callable[[], str],
    sudo_password: Callable[[], str],
) -> Connection:
    """Open a Fabric SSH connection.

    SSH: tries key/agent first; calls ssh_password() only if that fails.
    Sudo: probes `sudo -n true`; calls sudo_password() only if NOPASSWD isn't set.
    Each callback runs at most once, and only when its password is actually needed.
    """
    c = _build_connection(user, ip)
    try:
        c.open()
    except AuthenticationException:
        c = _build_connection(user, ip, password=ssh_password())
        c.open()

    if c.run("sudo -n true 2>/dev/null", hide=True, warn=True).failed:
        c.config.sudo.password = sudo_password()

    return c


def _build_connection(user: str, ip: str, password: str | None = None) -> Connection:
    kwargs = {"password": password} if password else {}
    c = Connection(host=ip, user=user, connect_kwargs=kwargs)
    if c.client is not None:
        c.client.set_missing_host_key_policy(AutoAddPolicy())
    else:
        log.warning(
            "Connection to %s@%s has no client; host key checking may not work",
            user,
            ip,
        )
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


def poll_until(
    check: Callable[[], tuple[bool, str]],
    description: str,
    timeout_s: float = 60.0,
    poll_s: float = 2.0,
) -> None:
    """Poll `check()` every `poll_s` seconds until it returns (True, _) or
    `timeout_s` elapses. On timeout, raises TimeoutError with the last error
    string returned by `check()` — so a consistently-failing probe surfaces
    loudly instead of spinning silently forever.

    `check()` returns (ok: bool, last_error: str). The caller decides which
    probe failures are retryable vs. hard — poll_until just times-them-out.
    """
    deadline = time.monotonic() + timeout_s
    last_error = "(probe never ran)"
    while time.monotonic() < deadline:
        ok, last_error = check()
        if ok:
            return
        time.sleep(poll_s)
    raise TimeoutError(
        f"{description}: timed out after {timeout_s:.0f}s.\n"
        f"Last error:\n{last_error}"
    )


def resolve_node_name(node_ip: str, wait_for: float = 0.0) -> str | None:
    """Return the k8s node name whose status.addresses contains `node_ip`.

    With `wait_for > 0`, poll the API server for that many seconds before giving up.
    Returns None if the node is genuinely not in the cluster within the window.

    Raises RuntimeError if `kubectl get nodes` fails consistently for the whole
    window — a broken API / TLS / auth error is a different failure mode from
    "node hasn't registered yet" and must surface loudly rather than masquerade
    as a missing node.
    """
    deadline = time.monotonic() + wait_for
    kubectl_ever_succeeded = False
    last_error = ""
    while True:
        result = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            kubectl_ever_succeeded = True
            data = json.loads(result.stdout)
            for item in data["items"]:
                for addr in item["status"]["addresses"]:
                    if addr["address"] == node_ip:
                        return item["metadata"]["name"]
        else:
            last_error = result.stderr
        if time.monotonic() >= deadline:
            if wait_for > 0 and not kubectl_ever_succeeded:
                raise RuntimeError(
                    f"kubectl get nodes failed consistently for {wait_for:.0f}s "
                    f"(cluster unreachable / TLS / auth). Last error:\n{last_error}"
                )
            return None
        time.sleep(2)


def install_prereqs(c: Connection) -> None:
    log.info(f"Installing prerequisites on {c.host}...")
    sudo_script(
        c,
        textwrap.dedent("""\
        set -euo pipefail
        if ! command -v nvidia-ctk &>/dev/null; then
            curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
                | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
            curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
                | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
                > /etc/apt/sources.list.d/nvidia-container-toolkit.list
            apt-get update
            apt-get install -y nvidia-container-toolkit
        fi
    """),
    )


def wipe_k3s_residue(c: Connection) -> None:
    """Remove directories k3s uninstallers do not always clean up."""
    sudo_script(
        c,
        textwrap.dedent("""\
        set -euo pipefail
        rm -rf /etc/rancher/k3s /var/lib/rancher/k3s /var/lib/kubelet /etc/cni /var/lib/cni
    """),
    )


def wipe_host_packages(c: Connection) -> None:
    """Inverse of install_prereqs: remove nvidia-container-toolkit + apt entries."""
    log.info(f"Removing host prerequisites on {c.host}...")
    sudo_script(
        c,
        textwrap.dedent("""\
        set -euo pipefail
        if dpkg -l nvidia-container-toolkit &>/dev/null; then
            apt-get remove --purge -y nvidia-container-toolkit
        fi
        apt-get autoremove -y || true
        rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list
        rm -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        apt-get update || true
    """),
    )


def await_node(node_ip: str) -> str:
    name = resolve_node_name(node_ip, wait_for=60.0)
    if name is None:
        sys.exit(f"Error: node with IP {node_ip} never registered with API server")
    return name


def kubectl(
    *args: str, check: bool = True, input: str | None = None, capture: bool = True
) -> str:
    result = subprocess.run(
        ["kubectl", *args],
        input=input,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        result.check_returncode()
    return result.stdout if capture else ""
