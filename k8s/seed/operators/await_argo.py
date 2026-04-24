"""AwaitArgo — gates setup progress until argocd-server is rolled out.

Setup-only: on teardown, argo dies with k3s via K3sServer.teardown.
"""

import logging
import subprocess
import time

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


log = logging.getLogger("k8s.seed.operators.await_argo")


class AwaitArgo(Operator):
    def setup(self, ctx: Context) -> None:
        log.info("Waiting for Argo server to come up...")
        while True:
            result = subprocess.run(
                ["kubectl", "-n", "argocd", "get", "deploy", "argocd-server"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break
            time.sleep(5)
        util.kubectl(
            "-n",
            "argocd",
            "rollout",
            "status",
            "deploy/argocd-server",
            "--timeout=5m",
            capture=False,
        )

    def teardown(self, ctx: Context) -> None:
        pass  # argo dies with k3s via K3sServer.teardown
