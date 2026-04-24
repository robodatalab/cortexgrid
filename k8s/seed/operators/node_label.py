"""NodeLabel — labels the node by role on setup; removes it from the API on teardown.

Required deps: node_ip.

`strict=True` (head) waits up to 60s and errors out if the node never registers.
`strict=False` (worker) allows the deferred-join case: if the node hasn't
registered yet (head wasn't up), the label is skipped.
"""

import logging
import subprocess

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.node_label")


class NodeLabel(Operator):
    def __init__(self, role: str, strict: bool = True):
        self.role = role
        self.strict = strict

    def setup(self, deps: dict) -> None:
        node_ip = deps["node_ip"]
        if self.strict:
            node_name = util.await_node(node_ip)
        else:
            node_name = util.resolve_node_name(node_ip, wait_for=5.0)
            if node_name is None:
                log.info(f"Node {node_ip} not yet registered; skipping label.")
                return
        try:
            log.info(f"Labelling node {node_name} with role={self.role}...")
            util.kubectl(
                "label", "node", node_name,
                f"role={self.role}", "--overwrite", capture=False,
            )
        except Exception as e:
            if self.strict:
                raise
            log.info(f"Warning: could not label node immediately ({e}).")

    def teardown(self, deps: dict) -> None:
        node_ip = deps["node_ip"]
        node_name = util.resolve_node_name(node_ip)
        if node_name:
            subprocess.run(["kubectl", "delete", "node", node_name], check=False)
