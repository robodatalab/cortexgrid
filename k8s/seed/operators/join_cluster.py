"""JoinCluster — joins a worker to the cluster.

Has two setup modes chosen at construction time:
  - mode="direct":   install k3s-agent now, pointing at the already-running head
  - mode="deferred": drop a systemd timer that will join once the head appears

Teardown always runs both cleanups (uninstall k3s-agent + remove deferred timer);
they're idempotent, and we may not know which setup path was actually used.

Required deps (setup, mode="direct"):   connection, head_ip, head_token
Required deps (setup, mode="deferred"): connection, aws_access_key_id, aws_secret_access_key
Required deps (teardown):                connection
"""

import logging
import shlex
import sys
import textwrap

from k8s.seed import util
from k8s.seed.pipeline import Operator


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
        if self.mode == "direct":
            self._direct_join(c, deps["head_ip"], deps["head_token"])
        else:
            self._deferred_join(
                c, deps["aws_access_key_id"], deps["aws_secret_access_key"]
            )

    def teardown(self, deps: dict) -> None:
        c = deps["connection"]
        self._uninstall_agent(c)
        self._remove_deferred_timer(c)

    def _direct_join(self, c, head_ip: str, head_token: str) -> None:
        log.info(f"Installing k3s agent on {c.host} → control-plane at {head_ip}...")
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            if [[ ! -x /usr/local/bin/k3s-agent ]] && [[ ! -x /usr/local/bin/k3s ]]; then
                curl -sfL https://get.k3s.io | K3S_URL={shlex.quote(f"https://{head_ip}:6443")} K3S_TOKEN={shlex.quote(head_token)} sh -
            fi
        """),
        )

    def _deferred_join(
        self, c, aws_access_key_id: str, aws_secret_access_key: str
    ) -> None:
        log.info(
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
            f'AWS_ACCESS_KEY_ID="{aws_access_key_id}"\n'
            f'AWS_SECRET_ACCESS_KEY="{aws_secret_access_key}"\n'
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
