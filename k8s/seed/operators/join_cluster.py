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
        if self.mode == "direct":
            self._direct_join(c, node_ip, deps["head_ip"], deps["head_token"])
        else:
            self._deferred_join(c, node_ip, deps["head_url"])

    def teardown(self, deps: dict) -> None:
        c = deps["connection"]
        self._uninstall_agent(c)
        self._remove_deferred_timer(c)
        util.unbind_k3s_from_tailscale(c, util.K3S_AGENT_UNIT)

    def _direct_join(self, c, node_ip: str, head_ip: str, head_token: str) -> None:
        log.info(f"Installing k3s agent on {c.host} → control-plane at {head_ip}...")
        # `node-ip` pins the agent to advertise its Tailscale IP; otherwise
        # k3s picks the LAN interface and downstream await_node lookups miss it.
        # `flannel-iface tailscale0` pins flannel's VXLAN underlay to Tailscale
        # so flannel.1's auto-derived MTU (~1230) fits inside Tailscale's 1280
        # MTU. Without this, cross-node pod traffic exceeds the tunnel and
        # large packets (DNS replies, TCP handshakes) get dropped.
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            mkdir -p /etc/rancher/k3s
            cat > /etc/rancher/k3s/config.yaml <<EOF
node-ip: {node_ip}
flannel-iface: tailscale0
EOF
            if [[ ! -x /usr/local/bin/k3s-agent ]] && [[ ! -x /usr/local/bin/k3s ]]; then
                curl -sfL https://get.k3s.io | K3S_URL={shlex.quote(f"https://{head_ip}:6443")} K3S_TOKEN={shlex.quote(head_token)} sh -
            fi
        """),
        )

    def _deferred_join(self, c, node_ip: str, head_url: str) -> None:
        log.info(
            f"Head not seeded yet — dropping systemd timer on {c.host} to join when it appears. "
            f"This command will now exit; the worker will join automatically."
        )

        # `node-ip` pins the agent to advertise its Tailscale IP when the timer
        # eventually runs `curl | sh -`; the file sits here waiting.
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            mkdir -p /etc/rancher/k3s
            cat > /etc/rancher/k3s/config.yaml <<EOF
node-ip: {node_ip}
flannel-iface: tailscale0
EOF
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

        env_content = f'CORTEXGRID_HEAD_URL="{head_url}"\n'

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
