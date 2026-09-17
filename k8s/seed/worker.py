"""Worker-role pipeline construction.

`mode` picks JoinCluster's setup path: "direct" (head is ready) or "deferred"
(head not ready yet — drop a join-when-possible timer). Teardown always cleans
both paths, so any valid mode works when building for a teardown call.

NodeLabel is strict in direct mode: the agent was just installed against a
running head, so a node that does not register is a failed join and must fail
setup. In deferred mode the node cannot have registered yet; it labels itself
(`node-label` in the agent config JoinCluster writes) when it joins.
ComputeLabels follows the same strictness.
"""

from typing import Literal

from k8s.seed import operators
from k8s.seed.pipeline import Pipeline


def build(*, mode: Literal["direct", "deferred"]) -> Pipeline:
    return Pipeline([
        operators.InstallPrereqs(),
        operators.JoinCluster(mode=mode),
        operators.NodeLabel(role="worker", strict=(mode == "direct")),
        operators.ComputeLabels(strict=(mode == "direct")),
    ])
