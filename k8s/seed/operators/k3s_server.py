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

import yaml  # type: ignore

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.k3s_server")

# Pin k3s. Without this, the installer pulls the current "stable" channel,
# so successive seeds silently bump the cluster's k8s minor and can break
# Argo CD's bundled OpenAPI (e.g. unknown Deployment .status fields).
K3S_VERSION = "v1.35.4+k3s1"


class K3sServer(Operator):
    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        bootstrap_file = deps["bootstrap_file"]
        node_ip = deps["node_ip"]
        profile = self._detect_profile(c)
        # Surface the detected profile for downstream operators (PlatformConfig)
        # so they don't have to re-SSH and parse /sys/class/dmi/id/sys_vendor.
        deps["profile"] = profile
        log.info(f"Installing k3s server on {c.host} (profile={profile})...")
        rendered = self._render_bootstrap(bootstrap_file.read_bytes(), profile)
        bootstrap_b64 = base64.b64encode(rendered).decode()
        # `tls-san` adds node_ip to the API server's TLS cert SANs.
        # `node-ip` tells k3s to register the node under node_ip instead of the
        # default LAN interface; otherwise pod networking and every script
        # that looks up the node by Tailscale IP (await_node) misses it.
        # `node-label role=head` registers the node with the label from the
        # very first kubelet registration. The bootstrap argocd manifest pins
        # its pods to `role=head`; without this, helm-install-argocd retries
        # repeatedly until NodeLabel runs ~minutes later in the pipeline,
        # leaving stale state in argocd-secret/argocd-cm that breaks the
        # application controller's gRPC client.
        # `flannel-iface tailscale0` pins flannel's VXLAN underlay to the
        # Tailscale interface. Without this, flannel auto-picks the LAN
        # interface (MTU 1500) and sets flannel.1 to MTU 1450; pod-to-pod
        # packets then exceed Tailscale's 1280 MTU and get dropped, breaking
        # cluster DNS and any cross-node TCP that produces large frames.
        util.sudo_script(
            c,
            textwrap.dedent(f"""\
            set -euo pipefail
            mkdir -p /var/lib/rancher/k3s/server/manifests /etc/rancher/k3s
            echo {shlex.quote(bootstrap_b64)} | base64 -d > /var/lib/rancher/k3s/server/manifests/argocd.yaml
            cat > /etc/rancher/k3s/config.yaml <<EOF
tls-san:
  - {node_ip}
node-ip: {node_ip}
flannel-iface: tailscale0
node-label:
  - role=head
EOF
            if [[ ! -x /usr/local/bin/k3s ]]; then
                curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION={shlex.quote(K3S_VERSION)} sh -
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

    def _detect_profile(self, c) -> str:
        """'aws' if the host is an EC2 instance, 'onprem' otherwise.

        Reads /sys/class/dmi/id/sys_vendor — populated by the BIOS/firmware,
        no network call. EC2 reports 'Amazon EC2'; physical hardware reports
        the actual vendor (LENOVO, NVIDIA, ...).
        """
        result = c.run("cat /sys/class/dmi/id/sys_vendor", hide=True, warn=True)
        vendor = result.stdout.strip() if result.ok else ""
        return "aws" if vendor == "Amazon EC2" else "onprem"

    def _render_bootstrap(self, content: bytes, profile: str) -> bytes:
        """Drop the bootstrap Application that does not match the detected profile.

        The shipped argocd.yaml declares both `argo-bootstrap-aws` and
        `argo-bootstrap-onprem`. Exactly one is staged into the cluster --
        whichever matches sys_vendor.
        """
        keep = f"argo-bootstrap-{profile}"
        kept = []
        for d in yaml.safe_load_all(content):
            if not d or d.get("kind") != "Application":
                kept.append(d)
                continue
            name = d.get("metadata", {}).get("name", "")
            if not name.startswith("argo-bootstrap-") or name == keep:
                kept.append(d)
        return yaml.safe_dump_all(kept).encode()
