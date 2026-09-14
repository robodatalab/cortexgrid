"""HeadServer -- runs the cortexgrid secrets server on the head.

Uploads scripts/cortexgrid_head.py plus a systemd unit and waits until the
server answers over the tailnet. It runs on the host rather than in k8s so it
is up before the cluster: every later operator and the External Secrets
Operator read and write secrets through it.

Secrets live in util.HEAD_ENV_PATH on the head. Teardown stops and removes the
server but keeps that file, so secrets survive a teardown/setup cycle.

Required deps (setup): connection, node_ip.
Required deps (teardown): connection.
"""

import logging
import textwrap
from pathlib import Path, PurePosixPath

import requests  # type: ignore

from k8s.seed import util
from k8s.seed.pipeline import Operator


SERVER_SCRIPT_SRC = Path(__file__).resolve().parent.parent / "scripts" / "cortexgrid_head.py"


log = logging.getLogger("k8s.seed.operators.head_server")


class HeadServer(Operator):
    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        url = util.head_url_for(deps["node_ip"])
        log.info(f"Installing cortexgrid head server on {c.host}...")

        service_unit = textwrap.dedent(f"""\
            [Unit]
            Description=cortexgrid: head secrets server
            After=network-online.target
            Wants=network-online.target

            [Service]
            ExecStart=/usr/bin/python3 {util.HEAD_SERVER_SCRIPT_PATH} --port {util.HEAD_SERVER_PORT} --env-file {util.HEAD_ENV_PATH}
            Restart=always
            RestartSec=2

            [Install]
            WantedBy=multi-user.target
        """)

        c.sudo(f"mkdir -p -m 700 {PurePosixPath(util.HEAD_ENV_PATH).parent}", hide=True)
        util.write_remote_file(
            c, SERVER_SCRIPT_SRC.read_text(), util.HEAD_SERVER_SCRIPT_PATH, mode="755"
        )
        util.write_remote_file(c, service_unit, util.HEAD_SERVER_SERVICE_PATH)
        c.sudo("systemctl daemon-reload", hide=True)
        c.sudo(f"systemctl enable {util.HEAD_SERVER_UNIT}", hide=True)
        # restart, not start: a re-run must pick up a changed script.
        c.sudo(f"systemctl restart {util.HEAD_SERVER_UNIT}", hide=True)

        def _reachable() -> tuple[bool, str]:
            try:
                requests.get(f"{url}/secrets", timeout=5).raise_for_status()
            except requests.RequestException as e:
                return False, str(e)
            return True, ""

        util.poll_until(_reachable, f"cortexgrid head server at {url}", timeout_s=60)

    def teardown(self, deps: dict) -> None:
        c = deps["connection"]
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            systemctl disable --now {util.HEAD_SERVER_UNIT} 2>/dev/null || true
            rm -f {util.HEAD_SERVER_SCRIPT_PATH} {util.HEAD_SERVER_SERVICE_PATH}
            systemctl daemon-reload
        """),
        )
