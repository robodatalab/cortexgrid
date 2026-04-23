"""Tear down a robolab cluster node.

Usage:
    uv run python k8s/seed/teardown-node.py --ip=X [--ssh-user=Z]

Wipes everything setup-node installed on the target node:
  - k3s (control plane or worker — detected automatically)
  - nvidia-container-toolkit, awscli, apt repo entries
  - any deferred-join systemd timer + bootstrap creds
Removes the node from the cluster and drops its entry from infra-config.yaml.
If the node was the head, also scrubs the local kubeconfig context and the
cluster-seed entries in AWS Secrets Manager.
"""

import argparse
import getpass
import subprocess
import sys
import textwrap

from botocore.exceptions import ClientError  # type: ignore
from dotenv import load_dotenv
from fabric import Connection  # type: ignore

from cortexflow.secrets import delete_secret
from k8s.seed import util


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tear down a robolab cluster node.")
    p.add_argument("--ip", required=True)
    p.add_argument("--ssh-user", default=getpass.getuser())
    return p.parse_args()


def wipe_node(c: Connection) -> None:
    util.sudo_script(
        c,
        textwrap.dedent(f"""\
        set -euo pipefail

        if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
            /usr/local/bin/k3s-uninstall.sh
        elif [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
            /usr/local/bin/k3s-agent-uninstall.sh
        fi

        # Deferred-join timer (present only if worker was seeded before head)
        if systemctl list-unit-files | grep -q robolab-join.timer; then
            systemctl disable --now robolab-join.timer || true
        fi
        rm -f {util.JOIN_ENV_PATH} {util.JOIN_SCRIPT_PATH} {util.JOIN_SERVICE_PATH} {util.JOIN_TIMER_PATH}
        systemctl daemon-reload || true

        if dpkg -l nvidia-container-toolkit &>/dev/null; then
            apt-get remove --purge -y nvidia-container-toolkit
        fi
        if dpkg -l awscli &>/dev/null; then
            apt-get remove --purge -y awscli
        fi
        apt-get autoremove -y || true

        rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list
        rm -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        apt-get update || true

        rm -rf /etc/rancher/k3s /var/lib/rancher/k3s /var/lib/kubelet /etc/cni /var/lib/cni
    """),
    )


def main() -> None:
    args = parse_args()
    cfg = util.load_config()
    entry = next((n for n in cfg["nodes"] if n["ip"] == args.ip), None)
    if entry is None:
        print(
            f"Warning: {args.ip} is not in infra-config.yaml — will still wipe the remote node."
        )
        role = None
    else:
        role = entry["role"]

    confirm = input(
        f"This will wipe k3s, nvidia-container-toolkit, and any deferred-join "
        f"timer on {args.ip}. Continue? [y/N] "
    )
    if confirm.strip().lower() != "y":
        sys.exit(0)

    sudo_pw = getpass.getpass("Node password (SSH + sudo): ")

    node_name = util.resolve_node_name(args.ip)

    with util.connect(args.ssh_user, args.ip, sudo_pw) as c:
        wipe_node(c)

    if node_name:
        subprocess.run(["kubectl", "delete", "node", node_name], check=False)

    cluster_info = subprocess.run(["kubectl", "cluster-info"], capture_output=True)
    head_gone = cluster_info.returncode != 0

    if head_gone or role == "head":
        for verb in ("delete-context", "delete-cluster", "delete-user"):
            subprocess.run(
                ["kubectl", "config", verb, util.KUBE_CONTEXT],
                capture_output=True,
                check=False,
            )
        print(f"Scrubbed kubeconfig context '{util.KUBE_CONTEXT}'.")

        load_dotenv(util.ENV_FILE)
        for sec in (util.SECRET_K3S_TOKEN, util.SECRET_CONTROL_PLANE_IP):
            try:
                delete_secret(sec)
                print(f"  deleted AWS SM: robolab/infra/{sec}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise

    if entry is not None:
        cfg["nodes"] = [n for n in cfg["nodes"] if n["ip"] != args.ip]
        util.save_config(cfg)
        print(f"Removed {args.ip} from infra-config.yaml.")

    print("Node teardown complete.")


if __name__ == "__main__":
    main()
