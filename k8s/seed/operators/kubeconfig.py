"""Kubeconfig — merges the head's kubeconfig into ~/.kube/config under a named context.

Required deps (setup): connection, node_ip.
Required deps (teardown): (none beyond instance config).
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.kubeconfig")


class Kubeconfig(Operator):
    def __init__(
        self,
        local_path: Path = Path.home() / ".kube" / "config",
        context: str = util.KUBE_CONTEXT,
    ):
        self.local_path = local_path
        self.context = context

    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        node_ip = deps["node_ip"]
        log.info(
            f"Merging kubeconfig into {self.local_path} as context '{self.context}'..."
        )
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_path.touch(exist_ok=True)
        self._scrub_local_context()

        remote_kcfg = c.sudo("cat /etc/rancher/k3s/k3s.yaml", hide=True).stdout
        remote_kcfg = (
            remote_kcfg.replace("127.0.0.1", node_ip)
            .replace("name: default", f"name: {self.context}")
            .replace(": default", f": {self.context}")
        )

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as f:
            f.write(remote_kcfg)
            tmp_path = f.name
        try:
            merged = subprocess.check_output(
                ["kubectl", "config", "view", "--flatten"],
                env={**os.environ, "KUBECONFIG": f"{self.local_path}:{tmp_path}"},
                text=True,
            )
            self.local_path.write_text(merged)
            self.local_path.chmod(0o600)
            util.kubectl("config", "use-context", self.context, capture=False)
        finally:
            os.unlink(tmp_path)

    def teardown(self, deps: dict) -> None:
        self._scrub_local_context()
        log.info(f"Scrubbed kubeconfig context '{self.context}'.")

    def _scrub_local_context(self) -> None:
        for verb in ("delete-context", "delete-cluster", "delete-user"):
            subprocess.run(
                ["kubectl", "config", verb, self.context],
                capture_output=True,
                check=False,
            )
