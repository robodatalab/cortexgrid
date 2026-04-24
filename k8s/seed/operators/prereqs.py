"""InstallPrereqs — nvidia-container-toolkit + awscli on the node."""

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


class InstallPrereqs(Operator):
    def setup(self, ctx: Context) -> None:
        util.install_prereqs(ctx.connection)

    def teardown(self, ctx: Context) -> None:
        util.wipe_host_packages(ctx.connection)
