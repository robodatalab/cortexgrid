"""NodeLabel — labels the node by role on setup; removes it from the API on teardown.

`strict=True` (head) waits up to 60s and errors out if the node never registers.
`strict=False` (worker) allows the deferred-join case: if the node hasn't
registered yet (head wasn't up), the label is skipped and the next head setup
will reconcile it via WorkerLabelsReconciler.
"""

import logging
import subprocess

from k8s.seed import util
from k8s.seed.pipeline import Context, Operator


log = logging.getLogger("k8s.seed.operators.node_label")


class NodeLabel(Operator):
    def __init__(self, role: str, strict: bool = True):
        self.role = role
        self.strict = strict

    def setup(self, ctx: Context) -> None:
        if self.strict:
            node_name = util.await_node(ctx.args.ip)
        else:
            node_name = util.resolve_node_name(ctx.args.ip, wait_for=5.0)
            if node_name is None:
                log.info(
                    f"Node {ctx.args.ip} not yet registered; skipping label "
                    f"(WorkerLabelsReconciler will handle it on next head seed)."
                )
                return
        try:
            log.info(f"Labelling node {node_name} with role={self.role}...")
            util.kubectl(
                "label",
                "node",
                node_name,
                f"role={self.role}",
                "--overwrite",
                capture=False,
            )
        except Exception as e:
            if self.strict:
                raise
            log.info(
                f"Warning: could not label node immediately ({e}); "
                f"will be reconciled on next head seed."
            )

    def teardown(self, ctx: Context) -> None:
        node_name = util.resolve_node_name(ctx.args.ip)
        if node_name:
            subprocess.run(["kubectl", "delete", "node", node_name], check=False)
