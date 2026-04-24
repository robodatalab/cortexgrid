"""InstallPrereqs — nvidia-container-toolkit + awscli on the node.

Required deps: connection.
"""

from k8s.seed import util
from k8s.seed.pipeline import Operator


class InstallPrereqs(Operator):
    def setup(self, deps: dict) -> None:
        util.install_prereqs(deps["connection"])

    def teardown(self, deps: dict) -> None:
        util.wipe_host_packages(deps["connection"])
