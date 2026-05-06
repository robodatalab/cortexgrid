"""TailscaleHostname -- sets the head's Tailscale machine name to robolab-head.

The terraform/tailnet-dns module CNAMEs every public subdomain
(argo, mlflow, cortexflow, ...) at robolab-head.<tailnet>, so any head
joining the tailnet must register under that name for DNS to resolve.

AWS EC2 sets it via cloud-init; on-prem boxes default to their machine
hostname. This operator unifies both paths -- idempotent on AWS, the
load-bearing rename on on-prem.

Required deps (setup): connection.
Required deps (teardown): (none -- name change is harmless after teardown).
"""

import logging

from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.tailscale_hostname")


_HOSTNAME = "robolab-head"


class TailscaleHostname(Operator):
    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        log.info(f"Setting Tailscale hostname to {_HOSTNAME} on {c.host}...")
        c.sudo(f"tailscale set --hostname={_HOSTNAME}", hide=True)

    def teardown(self, deps: dict) -> None:
        pass
