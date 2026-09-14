"""ControlPlaneDetails — publishes the k3s join token + service URLs to the head secrets store.

Required deps (setup): connection, node_ip.
Required deps (teardown): (none — deletion is by secret name).
"""

import logging

from cortexgrid.secrets import delete_secret, set_secret
from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.control_plane_details")


class ControlPlaneDetails(Operator):
    def setup(self, deps: dict) -> None:
        c = deps["connection"]
        node_ip = deps["node_ip"]
        log.info("Publishing k3s token + service URLs to the head secrets store...")
        token = c.sudo(
            "cat /var/lib/rancher/k3s/server/node-token", hide=True
        ).stdout.strip()
        set_secret(util.SECRET_K3S_TOKEN, token)
        set_secret(util.SECRET_CONTROL_PLANE_IP, node_ip)
        set_secret(util.SECRET_MLFLOW_TRACKING_URI, util.mlflow_tracking_uri_for(node_ip))
        set_secret(util.SECRET_RAY_JOB_SERVER_URI, util.ray_job_server_uri_for(node_ip))
        set_secret(util.SECRET_RAY_SERVE_URI, util.ray_serve_uri_for(node_ip))

    def teardown(self, deps: dict) -> None:
        log.info("Deleting k3s token + service URLs from the head secrets store...")
        delete_secret(util.SECRET_K3S_TOKEN)
        delete_secret(util.SECRET_CONTROL_PLANE_IP)
        delete_secret(util.SECRET_MLFLOW_TRACKING_URI)
        delete_secret(util.SECRET_RAY_JOB_SERVER_URI)
        delete_secret(util.SECRET_RAY_SERVE_URI)
