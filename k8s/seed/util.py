"""Shared helpers for the ./cg CLI (cli.py) and the operators.

Holds paths, constants, infra-config.yaml I/O, .env loading, Fabric SSH
helpers, and kubectl wrappers.
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
from dotenv import dotenv_values
from fabric import Connection  # type: ignore
from paramiko import AuthenticationException, AutoAddPolicy, SSHConfig  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / "infra-config.yaml"
# Head setup inputs nothing else can supply; see .env.head.template.
ENV_FILE = REPO_ROOT / ".env.head"
KUBE_CONTEXT = "robolab"

# Secret ids in the head secrets store.
SECRET_K3S_TOKEN = "K3S_NODE_TOKEN"
SECRET_CONTROL_PLANE_IP = "CONTROL_PLANE_TAILSCALE_IP"
SECRET_MLFLOW_TRACKING_URI = "MLFLOW_TRACKING_URI"
SECRET_RAY_JOB_SERVER_URI = "RAY_JOB_SERVER_URI"
SECRET_RAY_SERVE_URI = "RAY_SERVE_URI"
SECRET_JOBS_CONTROL_PLANE_URI = "JOBS_CONTROL_PLANE_URI"

# NodePorts must match the chart's Services (k8s/charts/cortexgrid). Seed pipeline
# stores the full URL in the head secrets store at setup time; downstream
# consumers read the URL, not the port.
_MLFLOW_NODEPORT = 30500
_RAY_DASHBOARD_NODEPORT = 30265
_RAY_SERVE_NODEPORT = 30000
_JOBS_CONTROL_PLANE_NODEPORT = 30700
_POSTGRES_NODEPORT = 30432
_MINIO_S3_NODEPORT = 30900


def mlflow_tracking_uri_for(tailscale_ip: str) -> str:
    return f"http://{tailscale_ip}:{_MLFLOW_NODEPORT}"


def ray_job_server_uri_for(tailscale_ip: str) -> str:
    return f"http://{tailscale_ip}:{_RAY_DASHBOARD_NODEPORT}"


def ray_serve_uri_for(tailscale_ip: str) -> str:
    return f"http://{tailscale_ip}:{_RAY_SERVE_NODEPORT}"


def jobs_control_plane_uri_for(tailscale_ip: str) -> str:
    return f"http://{tailscale_ip}:{_JOBS_CONTROL_PLANE_NODEPORT}"


def postgres_uri_for(tailscale_ip: str, db: str, user: str, password: str) -> str:
    return f"postgresql://{user}:{password}@{tailscale_ip}:{_POSTGRES_NODEPORT}/{db}"


def minio_s3_endpoint_for(tailscale_ip: str) -> str:
    return f"http://{tailscale_ip}:{_MINIO_S3_NODEPORT}"


# Head secrets server (HeadServer operator, scripts/cortexgrid_head.py). Runs on
# the head host, outside k8s, so it is up before the cluster.
HEAD_TAILNET_HOSTNAME = "robolab-head"
HEAD_SERVER_PORT = 7700
HEAD_SERVER_UNIT = "cortexgrid-head.service"
HEAD_SERVER_SCRIPT_PATH = "/usr/local/bin/cortexgrid-head.py"
HEAD_SERVER_SERVICE_PATH = f"/etc/systemd/system/{HEAD_SERVER_UNIT}"
# Outside /var/lib/rancher and STORAGE_PATH, which teardown wipes.
HEAD_ENV_PATH = "/etc/cortexgrid/.env"
# Selector-less Service whose EndpointSlice points at the head server. Pods reach
# it at http://cortexgrid-head.default.svc.cluster.local:7700.
HEAD_SERVICE_NAME = "cortexgrid-head"
HEAD_SERVICE_NAMESPACE = "default"

# Keys .env.head must provide. Head setup generates or looks up the rest.
_REQUIRED_HEAD_ENV = [
    "GH_TOKEN",
    "TAILSCALE_OPERATOR_CLIENT_ID",
    "TAILSCALE_OPERATOR_CLIENT_SECRET",
    "GH_APP_ID",
    "GH_APP_INSTALLATION_ID",
    "GH_APP_PRIVATE_KEY",
    "ROUTE53_ACCESS_KEY_ID",
    "ROUTE53_SECRET_ACCESS_KEY",
]


def head_url_for(host: str) -> str:
    return f"http://{host}:{HEAD_SERVER_PORT}"


def head_url(cfg: dict) -> str:
    """URL of the head secrets server for the cluster in infra-config.yaml.

    Uses the registered head's IP. Before a head is registered, falls back to the
    tailnet name TailscaleHostname gives the head during its setup.
    """
    head = cfg.get("head")
    return head_url_for(head["ip"] if head else HEAD_TAILNET_HOSTNAME)


def load_head_env() -> dict[str, str]:
    """Read .env.head; exit listing the required keys it lacks."""
    env = {k: v for k, v in dotenv_values(ENV_FILE).items() if v}
    missing = [k for k in _REQUIRED_HEAD_ENV if k not in env]
    if missing:
        sys.exit(
            f"Error: {ENV_FILE.name} is missing {', '.join(missing)}. "
            f"See .env.head.template."
        )
    return env

JOIN_SCRIPT_PATH = "/usr/local/bin/robolab-join.py"
JOIN_ENV_PATH = "/etc/default/robolab-bootstrap"
JOIN_SERVICE_PATH = "/etc/systemd/system/robolab-join.service"
JOIN_TIMER_PATH = "/etc/systemd/system/robolab-join.timer"


log = logging.getLogger("k8s.seed.util")


def load_config() -> dict:
    """infra-config.yaml: the `head` (absent until one is added) and the
    `workers` by alias, each entry with the setup steps it has `done`."""
    cfg: dict = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = yaml.safe_load(f) or {}
    cfg.setdefault("workers", {})
    return cfg


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
        # Container logs live in RAM, not on the OS disk: a vector/alloy
        # DaemonSet ships them to Loki within seconds, so the disk is only
        # touched as a WAL fallback when Loki is unreachable. This is a
        # hard requirement on workers (no large disk) and a sanity measure
        # on heads. Must run before k3s starts so kubelet creates pod
        # subdirs on the tmpfs, not under it.
        mkdir -p /var/log/pods
        if ! grep -q '^tmpfs /var/log/pods ' /etc/fstab; then
            echo 'tmpfs /var/log/pods tmpfs size=512M,mode=755 0 0' >> /etc/fstab
        fi
        mountpoint -q /var/log/pods || mount /var/log/pods
    """),
    )


# Pin k3s, for the head and every worker alike. Without this, the installer
# pulls the current "stable" channel, so successive seeds silently bump the
# cluster's k8s minor and can break Argo CD's bundled OpenAPI (e.g. unknown
# Deployment .status fields), and a worker joined later can run a newer k8s
# than the server, which Kubernetes does not support. The installers skip
# nodes that already have k3s, so bumping this also means re-seeding them.
K3S_VERSION = "v1.35.4+k3s1"

K3S_SERVER_UNIT = "k3s.service"
K3S_AGENT_UNIT = "k3s-agent.service"

# flannel's VXLAN device (flannel.1) is bound to tailscale0 (`flannel-iface`).
# Restarting tailscaled - e.g. `tailscale update` - recreates tailscale0, which
# deletes flannel.1, and k3s does not recreate it until k3s itself restarts:
# cross-node pod traffic stays broken. PartOf propagates tailscaled's restarts
# and stops to k3s; After/Wants start k3s only once tailscaled is up. k3s units
# run with KillMode=process, so restarting k3s leaves running pods in place.
K3S_TAILSCALE_DROPIN = textwrap.dedent("""\
    # Written by the cortexgrid seed: k8s/seed/util.py bind_k3s_to_tailscale.
    [Unit]
    After=tailscaled.service
    Wants=tailscaled.service
    PartOf=tailscaled.service
""")


def k3s_tailscale_dropin_path(unit: str) -> str:
    return f"/etc/systemd/system/{unit}.d/10-tailscale.conf"


def bind_k3s_to_tailscale(c: Connection, unit: str) -> None:
    """Install the K3S_TAILSCALE_DROPIN for the k3s `unit`. Safe to run before
    the unit is installed: systemd applies the drop-in once the unit appears."""
    path = k3s_tailscale_dropin_path(unit)
    sudo_script(
        c,
        "set -euo pipefail\n"
        f"mkdir -p {Path(path).parent}\n"
        f"cat > {path} <<'EOF'\n"
        f"{K3S_TAILSCALE_DROPIN}"
        "EOF\n"
        "systemctl daemon-reload\n",
    )


def unbind_k3s_from_tailscale(c: Connection, unit: str) -> None:
    """Remove the drop-in `bind_k3s_to_tailscale` installed for `unit`."""
    path = k3s_tailscale_dropin_path(unit)
    sudo_script(
        c,
        textwrap.dedent(f"""\
        set -euo pipefail
        rm -f {path}
        rmdir --ignore-fail-on-non-empty {Path(path).parent} 2>/dev/null || true
        systemctl daemon-reload || true
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


# `nvidia-smi` exit codes that mean the host has no GPU: not installed (127),
# "No devices were found" (6).
_NO_GPU_EXIT_CODES = (127, 6)


def has_gpu(c: Connection) -> bool:
    """True iff `nvidia-smi -L` on the host lists at least one GPU.

    Any other `nvidia-smi` failure (e.g. a driver/library version mismatch
    pending a reboot) exits instead of reporting no GPU, which would relabel a
    GPU node CPU-only.
    """
    result = c.run("nvidia-smi -L", hide=True, warn=True)
    if result.ok:
        return "GPU" in result.stdout
    if result.exited in _NO_GPU_EXIT_CODES:
        return False
    sys.exit(
        f"Error: `nvidia-smi -L` on {c.host} failed with exit code {result.exited}, "
        f"so it is unknown whether the host has a GPU. Fix the NVIDIA driver "
        f"(a reboot clears a driver/library version mismatch) and re-run.\n"
        f"{result.stdout}{result.stderr}"
    )


def compute_labels(gpu: bool) -> list[str]:
    """Node labels that place Ray workers: `worker=true` on every node that runs
    one, plus `gpu=true` to pick the GPU flavour over the CPU-only one."""
    return ["worker=true", "gpu=true"] if gpu else ["worker=true"]


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
