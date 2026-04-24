"""Seed a robolab cluster node.

Usage:
    uv run python k8s/seed/setup-node.py --type=head --ip=X --storage-path=Y [--ssh-user=Z]
    uv run python k8s/seed/setup-node.py --type=worker --ip=X [--ssh-user=Z]

All operations are idempotent. Topology is recorded in ./infra-config.yaml at
the repo root and is read by other infra scripts (teardown-node, etc.).
Run teardown-node first to change a node's role or config.

Worker-before-head is supported: when the head has not been seeded yet, the
worker command installs prerequisites, drops a systemd timer on the worker
that polls AWS Secrets Manager for the cluster's K3S_NODE_TOKEN and control-
plane IP, and joins automatically once those appear. The command returns
immediately — no re-run needed.
"""

import argparse
import base64
import getpass
import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import yaml  # type: ignore
from fabric import Connection  # type: ignore
from dotenv import load_dotenv, dotenv_values
from cortexflow.secrets import get_secret, list_secrets, set_secret
from k8s.seed import util


BOOTSTRAP_FILE = util.REPO_ROOT / "k8s" / "argocd.yaml"
KUBECONFIG_FILE = Path.home() / ".kube" / "config"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed a robolab cluster node.")
    p.add_argument("--type", required=True, choices=["head", "worker"])
    p.add_argument("--ip", required=True)
    p.add_argument(
        "--storage-path", help="Required for --type=head; rejected for --type=worker"
    )
    p.add_argument("--ssh-user", default=None)
    args = p.parse_args()

    if args.type == "head" and not args.storage_path:
        p.error("--storage-path is required when --type=head")
    if args.type == "worker" and args.storage_path:
        p.error("--storage-path is only valid when --type=head")

    if args.ssh_user is None:
        args.ssh_user = util.ssh_user_for_ip(args.ip) or getpass.getuser()

    return args


def validate_and_update(cfg: dict, args: argparse.Namespace) -> dict:
    existing = next((n for n in cfg["nodes"] if n["ip"] == args.ip), None)

    if existing is not None:
        if existing["role"] != args.type:
            sys.exit(
                f"Error: node {args.ip} is already registered as role={existing['role']}. "
                f"Run teardown-node first to change its role."
            )
        if args.type == "head" and existing.get("storage_path") != args.storage_path:
            sys.exit(
                f"Error: node {args.ip} is already registered with "
                f"storage_path={existing.get('storage_path')}. "
                f"Run teardown-node first to change storage_path."
            )

    if args.type == "head":
        other_head = next(
            (n for n in cfg["nodes"] if n["role"] == "head" and n["ip"] != args.ip),
            None,
        )
        if other_head is not None:
            sys.exit(
                f"Error: cluster already has a head at {other_head['ip']}. "
                f"Only one head is allowed — run teardown-node on the existing head first."
            )

    entry = {"ip": args.ip, "role": args.type}
    if args.type == "head":
        entry["storage_path"] = args.storage_path

    if existing is not None:
        cfg["nodes"] = [entry if n["ip"] == args.ip else n for n in cfg["nodes"]]
    else:
        cfg["nodes"].append(entry)

    return cfg


def install_prereqs(c: Connection) -> None:
    print(f"Installing prerequisites on {c.host}...")
    util.sudo_script(
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
        if ! command -v aws &>/dev/null; then
            apt-get update
            apt-get install -y awscli
        fi
    """),
    )


def install_k3s_server(c: Connection) -> None:
    print(f"Installing k3s server on {c.host} + staging argocd bootstrap...")
    bootstrap_b64 = base64.b64encode(BOOTSTRAP_FILE.read_bytes()).decode()
    util.sudo_script(
        c,
        textwrap.dedent(f"""\
        set -euo pipefail
        mkdir -p /var/lib/rancher/k3s/server/manifests
        echo {shlex.quote(bootstrap_b64)} | base64 -d > /var/lib/rancher/k3s/server/manifests/argocd.yaml
        if [[ ! -x /usr/local/bin/k3s ]]; then
            curl -sfL https://get.k3s.io | sh -
        fi
    """),
    )


def merge_kubeconfig(c: Connection, node_ip: str) -> None:
    print(
        f"Merging kubeconfig into {KUBECONFIG_FILE} as context '{util.KUBE_CONTEXT}'..."
    )
    KUBECONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    KUBECONFIG_FILE.touch(exist_ok=True)

    remote_kcfg = c.sudo("cat /etc/rancher/k3s/k3s.yaml", hide=True).stdout
    remote_kcfg = (
        remote_kcfg.replace("127.0.0.1", node_ip)
        .replace("name: default", f"name: {util.KUBE_CONTEXT}")
        .replace(": default", f": {util.KUBE_CONTEXT}")
    )

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as f:
        f.write(remote_kcfg)
        tmp_path = f.name
    try:
        merged = subprocess.check_output(
            ["kubectl", "config", "view", "--flatten"],
            env={**os.environ, "KUBECONFIG": f"{KUBECONFIG_FILE}:{tmp_path}"},
            text=True,
        )
        KUBECONFIG_FILE.write_text(merged)
        KUBECONFIG_FILE.chmod(0o600)
        util.kubectl("config", "use-context", util.KUBE_CONTEXT, capture=False)
    finally:
        os.unlink(tmp_path)


def wait_for_argo() -> None:
    print("Waiting for Argo server to come up...")
    while True:
        result = subprocess.run(
            ["kubectl", "-n", "argocd", "get", "deploy", "argocd-server"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break
        time.sleep(5)
    util.kubectl(
        "-n",
        "argocd",
        "rollout",
        "status",
        "deploy/argocd-server",
        "--timeout=5m",
        capture=False,
    )


def _await_node(node_ip: str) -> str:
    name = util.resolve_node_name(node_ip, wait_for=60.0)
    if name is None:
        sys.exit(f"Error: node with IP {node_ip} never registered with API server")
    return name


def patch_local_path_config(node_ip: str, storage_path: str) -> None:
    print(f"Configuring local-path-provisioner to use {storage_path} on {node_ip}...")
    while True:
        result = subprocess.run(
            ["kubectl", "-n", "kube-system", "get", "cm", "local-path-config"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break
        time.sleep(2)
    node_name = _await_node(node_ip)
    config_json = json.dumps(
        {
            "nodePathMap": [
                {
                    "node": "DEFAULT_PATH_FOR_NON_LISTED_NODES",
                    "paths": ["/var/lib/rancher/k3s/storage"],
                },
                {"node": node_name, "paths": [storage_path]},
            ]
        }
    )
    patch = yaml.safe_dump({"data": {"config.json": config_json}})
    util.kubectl(
        "-n",
        "kube-system",
        "patch",
        "cm",
        "local-path-config",
        "--type=merge",
        "--patch",
        patch,
        capture=False,
    )


def publish_env_to_aws_sm() -> None:
    print("Publishing .env entries to AWS Secrets Manager at robolab/infra/*...")
    for k, v in dotenv_values(util.ENV_FILE).items():
        if v is None:
            continue
        set_secret(k, v)
        print(f"  published robolab/infra/{k}")


def seed_bootstrap_secrets() -> None:
    print("Bootstrap: GitHub repo credentials in argocd namespace...")
    github_secret = textwrap.dedent(f"""\
        apiVersion: v1
        kind: Secret
        metadata:
          name: argo-github-repo
          namespace: argocd
          labels:
            argocd.argoproj.io/secret-type: repository
        type: Opaque
        stringData:
          type: git
          url: https://github.com/paksas/robolab-infra.git
          username: x-access-token
          password: "{os.environ["GH_TOKEN"]}"
    """)
    util.kubectl("apply", "-f", "-", input=github_secret, capture=False)

    print("Seeding AWS bootstrap credentials for ESO...")
    aws_secret = textwrap.dedent(f"""\
        apiVersion: v1
        kind: Namespace
        metadata:
          name: external-secrets
        ---
        apiVersion: v1
        kind: Secret
        metadata:
          name: aws-bootstrap-creds
          namespace: external-secrets
        type: Opaque
        stringData:
          AWS_ACCESS_KEY_ID: "{os.environ["AWS_ACCESS_KEY_ID"]}"
          AWS_SECRET_ACCESS_KEY: "{os.environ["AWS_SECRET_ACCESS_KEY"]}"
    """)
    util.kubectl("apply", "-f", "-", input=aws_secret, capture=False)


def publish_control_plane_details(c: Connection, node_ip: str) -> None:
    print("Publishing k3s token + control-plane IP to AWS SM...")
    token = c.sudo(
        "cat /var/lib/rancher/k3s/server/node-token", hide=True
    ).stdout.strip()
    set_secret(util.SECRET_K3S_TOKEN, token)
    set_secret(util.SECRET_CONTROL_PLANE_IP, node_ip)


def label_node(node_ip: str, role: str) -> None:
    name = _await_node(node_ip)
    print(f"Labelling node {name} with role={role}...")
    util.kubectl("label", "node", name, f"role={role}", "--overwrite", capture=False)


def setup_head(args: argparse.Namespace, cfg: dict) -> None:
    load_dotenv(util.ENV_FILE)
    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")

    with util.connect(args.ssh_user, args.ip, sudo_pw) as c:
        install_prereqs(c)
        install_k3s_server(c)
        merge_kubeconfig(c, args.ip)
        wait_for_argo()
        patch_local_path_config(args.ip, args.storage_path)
        publish_env_to_aws_sm()
        seed_bootstrap_secrets()
        publish_control_plane_details(c, args.ip)

    label_node(args.ip, "head")
    reconcile_worker_labels(cfg)
    print(
        f"\nHead seeded.\n  Argo UI: http://{args.ip}:30080 (auth disabled, via Tailscale)"
    )


def install_k3s_agent_direct(c: Connection, head_ip: str, token: str) -> None:
    print(f"Installing k3s agent on {c.host} → control-plane at {head_ip}...")
    util.sudo_script(
        c,
        textwrap.dedent(f"""\
        set -euo pipefail
        if [[ ! -x /usr/local/bin/k3s-agent ]] && [[ ! -x /usr/local/bin/k3s ]]; then
            curl -sfL https://get.k3s.io | K3S_URL={shlex.quote(f"https://{head_ip}:6443")} K3S_TOKEN={shlex.quote(token)} sh -
        fi
    """),
    )


def install_deferred_join(c: Connection) -> None:
    print(
        f"Head not seeded yet — dropping systemd timer on {c.host} to join when it appears. "
        f"This command will now exit; the worker will join automatically."
    )

    join_script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -u
        [ -f {util.JOIN_ENV_PATH} ] || exit 0
        . {util.JOIN_ENV_PATH}
        export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION=us-east-1

        TOKEN=$(aws secretsmanager get-secret-value --secret-id robolab/infra/{util.SECRET_K3S_TOKEN} --query SecretString --output text 2>/dev/null) || exit 0
        HEAD_IP=$(aws secretsmanager get-secret-value --secret-id robolab/infra/{util.SECRET_CONTROL_PLANE_IP} --query SecretString --output text 2>/dev/null) || exit 0
        [ -z "$TOKEN" ] || [ -z "$HEAD_IP" ] && exit 0

        curl -sfL https://get.k3s.io | K3S_URL="https://${{HEAD_IP}}:6443" K3S_TOKEN="$TOKEN" sh -

        systemctl disable --now robolab-join.timer
        rm -f {util.JOIN_ENV_PATH} {util.JOIN_SCRIPT_PATH} {util.JOIN_SERVICE_PATH} {util.JOIN_TIMER_PATH}
        systemctl daemon-reload
        echo "Joined robolab cluster."
    """)

    service_unit = textwrap.dedent("""\
        [Unit]
        Description=robolab: join cluster when head is available

        [Service]
        Type=oneshot
        ExecStart=/usr/local/bin/robolab-join.sh
    """)

    timer_unit = textwrap.dedent("""\
        [Unit]
        Description=robolab: periodically attempt to join cluster

        [Timer]
        OnBootSec=60s
        OnUnitActiveSec=60s
        Unit=robolab-join.service

        [Install]
        WantedBy=timers.target
    """)

    env_content = (
        f'AWS_ACCESS_KEY_ID="{os.environ["AWS_ACCESS_KEY_ID"]}"\n'
        f'AWS_SECRET_ACCESS_KEY="{os.environ["AWS_SECRET_ACCESS_KEY"]}"\n'
    )

    util.write_remote_file(c, env_content, util.JOIN_ENV_PATH, mode="600")
    util.write_remote_file(c, join_script, util.JOIN_SCRIPT_PATH, mode="755")
    util.write_remote_file(c, service_unit, util.JOIN_SERVICE_PATH)
    util.write_remote_file(c, timer_unit, util.JOIN_TIMER_PATH)

    c.sudo("systemctl daemon-reload", hide=True)
    c.sudo("systemctl enable --now robolab-join.timer", hide=True)


def reconcile_worker_labels(cfg: dict) -> None:
    """Label any joined worker node per infra-config.yaml (best-effort; silent if cluster unreachable)."""
    for node in cfg["nodes"]:
        if node["role"] != "worker":
            continue
        info = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "json"],
            capture_output=True,
            text=True,
        )
        if info.returncode != 0:
            return
        data = json.loads(info.stdout)
        for item in data["items"]:
            if any(a["address"] == node["ip"] for a in item["status"]["addresses"]):
                util.kubectl(
                    "label",
                    "node",
                    item["metadata"]["name"],
                    "role=worker",
                    "--overwrite",
                    capture=False,
                )


def setup_worker(args: argparse.Namespace, cfg: dict) -> None:
    load_dotenv(util.ENV_FILE)
    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")

    available = set(list_secrets())
    head_ready = (
        util.SECRET_K3S_TOKEN in available and util.SECRET_CONTROL_PLANE_IP in available
    )
    token = get_secret(util.SECRET_K3S_TOKEN) if head_ready else None
    head_ip = get_secret(util.SECRET_CONTROL_PLANE_IP) if head_ready else None

    with util.connect(args.ssh_user, args.ip, sudo_pw) as c:
        install_prereqs(c)
        if token and head_ip:
            install_k3s_agent_direct(c, head_ip, token)
        else:
            install_deferred_join(c)

    if token and head_ip:
        try:
            label_node(args.ip, "worker")
        except Exception as e:
            print(
                f"Warning: could not label node immediately ({e}); will be reconciled on next head seed."
            )
        print(f"\nWorker seeded and joined cluster at {head_ip}.")
    else:
        print(
            f"\nWorker prerequisites installed and deferred-join timer active on {args.ip}. "
            f"The worker will join the cluster automatically within ~60s of the head being seeded."
        )


def main() -> None:
    args = parse_args()
    cfg = util.load_config()
    cfg = validate_and_update(cfg, args)
    util.save_config(cfg)

    if args.type == "head":
        setup_head(args, cfg)
    else:
        setup_worker(args, cfg)


if __name__ == "__main__":
    main()
