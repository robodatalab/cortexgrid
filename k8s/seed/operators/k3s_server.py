"""K3sServer — installs k3s on the head, stages the argocd bootstrap manifest.

Required deps (setup): connection, bootstrap_file.
Required deps (teardown): connection.

Setup is not considered done until k3s has applied its staged manifests and
argocd-server has rolled out. The readiness check runs on the head itself
via `k3s kubectl`, so it does not depend on the local kubeconfig.
"""

import base64
import logging
import shlex
import textwrap
import time

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.k3s_server")


class K3sServer(Operator):
    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        bootstrap_file = deps["bootstrap_file"]
        log.info(f"Installing k3s server on {c.host} + staging argocd bootstrap...")
        bootstrap_b64 = base64.b64encode(bootstrap_file.read_bytes()).decode()
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
        self._await_bootstrap_applied(c)

    def teardown(self, deps: dict) -> None:
        c = deps["connection"]
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

    def _await_bootstrap_applied(self, c) -> None:
        log.info("Waiting for k3s to apply the argocd bootstrap manifest...")
        while True:
            result = c.sudo(
                "k3s kubectl -n argocd get deploy argocd-server",
                hide=True,
                warn=True,
            )
            if result.ok:
                break
            time.sleep(5)
        c.sudo(
            "k3s kubectl -n argocd rollout status deploy/argocd-server --timeout=5m",
            hide=True,
        )
