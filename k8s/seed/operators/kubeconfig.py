"""Kubeconfig — merges the head's kubeconfig into ~/.kube/config as the 'robolab' context."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


KUBECONFIG_FILE = Path.home() / ".kube" / "config"

log = logging.getLogger("k8s.seed.operators.kubeconfig")


class Kubeconfig(Operator):
    def setup(self, ctx: Context) -> None:
        c = ctx.connection
        node_ip = ctx.args.ip
        log.info(
            f"Merging kubeconfig into {KUBECONFIG_FILE} as context '{util.KUBE_CONTEXT}'..."
        )
        KUBECONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        KUBECONFIG_FILE.touch(exist_ok=True)

        remote_kcfg = c.sudo("cat /etc/rancher/k3s/k3s.yaml", hide=True).stdout
        remote_kcfg = (
            remote_kcfg.replace("127.0.0.1", node_ip)
            .replace("name: default", f"name: {util.KUBE_CONTEXT}")
            .replace(": default", f": {util.KUBE_CONTEXT}")
        )

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as f:
            f.write(remote_kcfg)
            tmp_path = f.name
        try:
            merged = subprocess.check_output(
                ["kubectl", "config", "view", "--flatten"],
                env={**os.environ, "KUBECONFIG": f"{KUBECONFIG_FILE}:{tmp_path}"},
                text=True,
            )
            KUBECONFIG_FILE.write_text(merged)
            KUBECONFIG_FILE.chmod(0o600)
            util.kubectl("config", "use-context", util.KUBE_CONTEXT, capture=False)
        finally:
            os.unlink(tmp_path)

    def teardown(self, ctx: Context) -> None:
        for verb in ("delete-context", "delete-cluster", "delete-user"):
            subprocess.run(
                ["kubectl", "config", verb, util.KUBE_CONTEXT],
                capture_output=True,
                check=False,
            )
        log.info(f"Scrubbed kubeconfig context '{util.KUBE_CONTEXT}'.")
