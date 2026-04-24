"""K3sServer — installs k3s on the head, stages the argocd bootstrap manifest.

Required deps (setup): connection, bootstrap_file, node_ip.
Required deps (teardown): connection.

Setup is not considered done until the argocd namespace exists — downstream
operators (BootstrapSecrets) need it to apply Secrets. We intentionally do NOT
wait for argocd-server to roll out here: the bootstrap manifest pins argocd
pods to `role=head`, which NodeLabel applies later in the pipeline. Waiting
for rollout in K3sServer would deadlock.

The readiness check runs on the head itself via `k3s kubectl`, so it does not
depend on the local kubeconfig.
"""

import base64
import logging
import shlex
import textwrap

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.k3s_server")


class K3sServer(Operator):
    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        bootstrap_file = deps["bootstrap_file"]
        node_ip = deps["node_ip"]
        log.info(f"Installing k3s server on {c.host} + staging argocd bootstrap...")
        bootstrap_b64 = base64.b64encode(bootstrap_file.read_bytes()).decode()
        # `tls-san` tells k3s to include node_ip in the API server's TLS cert
        # SANs; without it, kubectl over Tailscale fails verification.
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            mkdir -p /var/lib/rancher/k3s/server/manifests /etc/rancher/k3s
            echo {shlex.quote(bootstrap_b64)} | base64 -d > /var/lib/rancher/k3s/server/manifests/argocd.yaml
            cat > /etc/rancher/k3s/config.yaml <<EOF
tls-san:
  - {node_ip}
EOF
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
        """Wait only until the argocd namespace exists.

        We deliberately do NOT wait for argocd-server to roll out: the bootstrap
        manifest pins argocd pods to `role=head`, and that label is applied by
        NodeLabel further down the pipeline. Waiting for rollout here would
        deadlock. Downstream operators only need the namespace to exist.
        """
        log.info("Waiting for argocd namespace to exist...")

        def _namespace_ready() -> tuple[bool, str]:
            result = c.sudo("k3s kubectl get ns argocd", hide=True, warn=True)
            return result.ok, result.stderr

        util.poll_until(
            _namespace_ready,
            "argocd namespace on the head",
            timeout_s=300,
            poll_s=5,
        )
