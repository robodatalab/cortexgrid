"""K3sAgentDirect — joins a worker to an already-running head.

Setup is a no-op if the head isn't ready yet (DeferredJoin handles that case).
Teardown runs k3s-agent-uninstall unconditionally (idempotent).
"""

import logging
import shlex
import textwrap

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


log = logging.getLogger("k8s.seed.operators.k3s_agent_direct")


class K3sAgentDirect(Operator):
    def setup(self, ctx: Context) -> None:
        if not (ctx.head_ip and ctx.head_token):
            return
        c = ctx.connection
        log.info(f"Installing k3s agent on {c.host} → control-plane at {ctx.head_ip}...")
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            if [[ ! -x /usr/local/bin/k3s-agent ]] && [[ ! -x /usr/local/bin/k3s ]]; then
                curl -sfL https://get.k3s.io | K3S_URL={shlex.quote(f"https://{ctx.head_ip}:6443")} K3S_TOKEN={shlex.quote(ctx.head_token)} sh -
            fi
        """),
        )

    def teardown(self, ctx: Context) -> None:
        c = ctx.connection
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
