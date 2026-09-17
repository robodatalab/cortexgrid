"""ComputeLabels — labels the node for the Ray worker DaemonSets on setup; unlabels on teardown.

Required deps: connection, node_ip.

Setup probes the host with `nvidia-smi` and applies `worker=true`, plus
`gpu=true` on a GPU host (removed otherwise), so the GPU or the CPU-only
ray-worker DaemonSet lands there. Runs via kubectl on every setup, so a re-run
labels a node that registered before these labels existed (k3s applies the
agent config's `node-label` only at registration).

`strict=True` waits up to 60s and errors out if the node never registers.
`strict=False` allows the deferred-join case: if the node hasn't registered yet
(head wasn't up), the labels are skipped - the agent config carries them.
"""

import logging

from k8s.seed import util
from k8s.seed.pipeline import Operator


log = logging.getLogger("k8s.seed.operators.compute_labels")


class ComputeLabels(Operator):
    def __init__(self, strict: bool = True):
        self.strict = strict

    def setup(self, deps: dict) -> None:
        node_ip = deps["node_ip"]
        if self.strict:
            node_name = util.await_node(node_ip)
        else:
            node_name = util.resolve_node_name(node_ip, wait_for=5.0)
            if node_name is None:
                log.info(f"Node {node_ip} not yet registered; skipping compute labels.")
                return
        gpu = util.has_gpu(deps["connection"])
        labels = util.compute_labels(gpu) + ([] if gpu else ["gpu-"])
        log.info(f"Labelling node {node_name} with {' '.join(labels)}...")
        util.kubectl("label", "node", node_name, *labels, "--overwrite", capture=False)

    def teardown(self, deps: dict) -> None:
        node_name = util.resolve_node_name(deps["node_ip"])
        if node_name:
            util.kubectl(
                "label", "node", node_name, "worker-", "gpu-",
                capture=False, check=False,
            )
