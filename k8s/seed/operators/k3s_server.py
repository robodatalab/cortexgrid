"""K3sServer — installs k3s on the head, stages the argocd bootstrap manifest."""

import base64
import logging
import shlex
import textwrap

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


BOOTSTRAP_FILE = util.REPO_ROOT / "k8s" / "argocd.yaml"

log = logging.getLogger("k8s.seed.operators.k3s_server")


class K3sServer(Operator):
    def setup(self, ctx: Context) -> None:
        c = ctx.connection
        log.info(f"Installing k3s server on {c.host} + staging argocd bootstrap...")
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

    def teardown(self, ctx: Context) -> None:
        c = ctx.connection
        util.sudo_script(
            c,
            textwrap.dedent("""\
            set -euo pipefail
            if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
                /usr/local/bin/k3s-uninstall.sh
            fi
        """),
        )
        util.wipe_k3s_residue(c)
