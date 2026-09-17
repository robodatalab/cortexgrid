"""JoinCluster — joins a worker to the cluster.

Has two setup modes chosen at construction time:
  - mode="direct":   install k3s-agent now, pointing at the already-running head
  - mode="deferred": drop a systemd timer that polls the head secrets server at
                     head_url; the timer runs scripts/robolab_join.py, which
                     installs k3s-agent and self-removes once the head's
                     token + IP appear there.

Teardown always runs both cleanups (uninstall k3s-agent + remove deferred
timer); they're idempotent, and we may not know which setup path was actually
used.

Required deps (setup, mode="direct"):   connection, node_ip, head_ip, head_token
Required deps (setup, mode="deferred"): connection, node_ip, head_url
Required deps (teardown):                connection
"""

import logging
import shlex
import sys
import textwrap
from pathlib import Path

from k8s.seed import util
from k8s.seed.pipeline import Operator


JOIN_SCRIPT_SRC = Path(__file__).resolve().parent.parent / "scripts" / "robolab_join.py"


log = logging.getLogger("k8s.seed.operators.join_cluster")


def _agent_config(node_ip: str, gpu: bool) -> str:
    """/etc/rancher/k3s/config.yaml for a worker's k3s agent, read whenever the
    agent is installed - now (direct) or later by the join timer (deferred).

    `node-ip` pins the agent to advertise its Tailscale IP; otherwise k3s picks
    the LAN interface and downstream await_node lookups miss it.
    `flannel-iface tailscale0` pins flannel's VXLAN underlay to Tailscale so
    flannel.1's auto-derived MTU (~1230) fits inside Tailscale's 1280 MTU.
    Without this, cross-node pod traffic exceeds the tunnel and large packets
    (DNS replies, TCP handshakes) get dropped.
    `node-label` registers the node already labelled, whenever it joins: the
    ray-worker DaemonSets select `worker=true` (+ `gpu`), and neither NodeLabel
    / ComputeLabels (which may run before a deferred worker joins) nor the
    head's WorkerLabels (which runs before deferred workers see the head)
    reliably labels it later. k3s applies `node-label` only at registration;
    ComputeLabels re-applies the compute labels on a re-run.
    """
    labels = "".join(f"  - {label}\n" for label in ["role=worker", *util.compute_labels(gpu)])
    return (
        f"node-ip: {node_ip}\n"
        "flannel-iface: tailscale0\n"
        "node-label:\n"
        f"{labels}"
    )


class JoinCluster(Operator):
    def __init__(self, mode: str):
        if mode not in ("direct", "deferred"):
            raise ValueError(
                f"JoinCluster mode must be 'direct' or 'deferred', got {mode!r}"
            )
        self.mode = mode

    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        node_ip = deps["node_ip"]
        # Before either join path installs the agent (now, or later from the
        # deferred timer), so its first start is already ordered after tailscaled.
        util.bind_k3s_to_tailscale(c, util.K3S_AGENT_UNIT)
        gpu = util.has_gpu(c)
        if self.mode == "direct":
            self._direct_join(c, node_ip, gpu, deps["head_ip"], deps["head_token"])
        else:
            self._deferred_join(c, node_ip, gpu, deps["head_url"])

    def teardown(self, deps: dict) -> None:
        c = deps["connection"]
        self._uninstall_agent(c)
        self._remove_deferred_timer(c)
        util.unbind_k3s_from_tailscale(c, util.K3S_AGENT_UNIT)

    def _direct_join(
        self, c, node_ip: str, gpu: bool, head_ip: str, head_token: str
    ) -> None:
        log.info(f"Installing k3s agent on {c.host} → control-plane at {head_ip}...")
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            mkdir -p /etc/rancher/k3s
            cat > /etc/rancher/k3s/config.yaml <<EOF
{_agent_config(node_ip, gpu)}EOF
            if [[ ! -x /usr/local/bin/k3s-agent ]] && [[ ! -x /usr/local/bin/k3s ]]; then
                curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION={shlex.quote(util.K3S_VERSION)} K3S_URL={shlex.quote(f"https://{head_ip}:6443")} K3S_TOKEN={shlex.quote(head_token)} sh -
            fi
        """),
        )

    def _deferred_join(self, c, node_ip: str, gpu: bool, head_url: str) -> None:
        log.info(
            f"Head not seeded yet — dropping systemd timer on {c.host} to join when it appears. "
            f"This command will now exit; the worker will join automatically."
        )

        # The agent config sits here until the timer runs `curl | sh -`.
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            mkdir -p /etc/rancher/k3s
            cat > /etc/rancher/k3s/config.yaml <<EOF
{_agent_config(node_ip, gpu)}EOF
        """),
        )

        join_script = JOIN_SCRIPT_SRC.read_text()

        service_unit = textwrap.dedent(f"""\
            [Unit]
            Description=robolab: join cluster when head is available

            [Service]
            Type=oneshot
            ExecStart=/usr/bin/python3 {util.JOIN_SCRIPT_PATH}
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

        # K3S_VERSION: the timer script cannot import util, so the pin travels here.
        env_content = (
            f'CORTEXGRID_HEAD_URL="{head_url}"\n'
            f'K3S_VERSION="{util.K3S_VERSION}"\n'
        )

        util.write_remote_file(c, env_content, util.JOIN_ENV_PATH, mode="600")
        util.write_remote_file(c, join_script, util.JOIN_SCRIPT_PATH, mode="755")
        util.write_remote_file(c, service_unit, util.JOIN_SERVICE_PATH)
        util.write_remote_file(c, timer_unit, util.JOIN_TIMER_PATH)

        c.sudo("systemctl daemon-reload", hide=True)
        c.sudo("systemctl enable --now robolab-join.timer", hide=True)

        state = c.run(
            "systemctl is-active robolab-join.timer", hide=True, warn=True
        ).stdout.strip()
        if state != "active":
            sys.exit(
                f"Error: robolab-join.timer on {c.host} did not reach 'active' state "
                f"(got '{state}'). Check `systemctl status robolab-join.timer` on the node."
            )
        print(f"robolab-join.timer is active on {c.host}.")

    def _uninstall_agent(self, c) -> None:
        util.sudo_script(
            c,
            textwrap.dedent("""\
            set -euo pipefail
            if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then
                /usr/local/bin/k3s-agent-uninstall.sh
            fi
        """),
        )
        util.wipe_k3s_residue(c)

    def _remove_deferred_timer(self, c) -> None:
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            if systemctl list-unit-files | grep -q robolab-join.timer; then
                systemctl disable --now robolab-join.timer || true
            fi
            rm -f {util.JOIN_ENV_PATH} {util.JOIN_SCRIPT_PATH} {util.JOIN_SERVICE_PATH} {util.JOIN_TIMER_PATH}
            systemctl daemon-reload || true
        """),
        )
