"""ArgoReady — bounce the argocd-application-controller to clear bootstrap-time wedges.

During the very first helm install, the controller pod can race against the
`argocd-redis-secret-init` Job and `argocd-repo-server` startup. When that
happens it logs "secretkey is missing" and "connection refused", then never
retries — its main reconcile loop wedges and `argo-bootstrap` stays Unknown
forever.

A single targeted restart after everything else has settled clears this. The
new pod reads fresh state and reconciles cleanly.

Required deps (setup): (none — uses local kubectl via the merged kubeconfig).
Required deps (teardown): (none — nothing to undo).
"""

import logging
import subprocess

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.argo_ready")

POD = "argocd-application-controller-0"
NAMESPACE = "argocd"


def _is_ready() -> tuple[bool, str]:
    result = subprocess.run(
        ["kubectl", "get", "pod", "-n", NAMESPACE, POD,
         "-o", "jsonpath={.status.containerStatuses[0].ready}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, result.stderr or result.stdout
    return result.stdout.strip() == "true", "pod not yet ready"


class ArgoReady(Operator):
    def setup(self, deps: dict) -> None:
        log.info(f"Waiting for {POD} to be Ready before bouncing it...")
        util.poll_until(_is_ready, f"{POD} Ready", timeout_s=300, poll_s=5)
        log.info(f"Bouncing {POD} to clear any bootstrap-time wedges...")
        util.kubectl("delete", "pod", "-n", NAMESPACE, POD, capture=False)
        util.poll_until(_is_ready, f"{POD} Ready after bounce", timeout_s=180, poll_s=5)

    def teardown(self, deps: dict) -> None:
        pass
