"""DeferredJoin — drops a systemd timer that joins the cluster when the head appears.

Setup writes the script + timer only if the head ISN'T ready yet.
Teardown removes them unconditionally (idempotent).
"""

import logging
import os
import sys
import textwrap

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


log = logging.getLogger("k8s.seed.operators.deferred_join")


class DeferredJoin(Operator):
    def setup(self, ctx: Context) -> None:
        if ctx.head_ip and ctx.head_token:
            return
        c = ctx.connection
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
            f'AWS_ACCESS_KEY_ID="{os.environ["AWS_ACCESS_KEY_ID"]}"\n'
            f'AWS_SECRET_ACCESS_KEY="{os.environ["AWS_SECRET_ACCESS_KEY"]}"\n'
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

    def teardown(self, ctx: Context) -> None:
        c = ctx.connection
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
