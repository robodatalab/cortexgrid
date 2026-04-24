"""ControlPlaneDetails — publishes the k3s join token + control-plane IP to AWS SM."""

import logging

from botocore.exceptions import ClientError  # type: ignore

from cortexflow.secrets import delete_secret, set_secret
from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


log = logging.getLogger("k8s.seed.operators.control_plane_details")


class ControlPlaneDetails(Operator):
    def setup(self, ctx: Context) -> None:
        c = ctx.connection
        node_ip = ctx.args.ip
        log.info("Publishing k3s token + control-plane IP to AWS SM...")
        token = c.sudo(
            "cat /var/lib/rancher/k3s/server/node-token", hide=True
        ).stdout.strip()
        set_secret(util.SECRET_K3S_TOKEN, token)
        set_secret(util.SECRET_CONTROL_PLANE_IP, node_ip)

    def teardown(self, ctx: Context) -> None:
        for sec in (util.SECRET_K3S_TOKEN, util.SECRET_CONTROL_PLANE_IP):
            try:
                delete_secret(sec)
                log.info(f"  deleted AWS SM: robolab/infra/{sec}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
