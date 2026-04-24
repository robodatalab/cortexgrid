"""Worker-role pipeline construction.

`mode` picks JoinCluster's setup path: "direct" (head is ready) or "deferred"
(head not ready yet — drop a join-when-possible timer). Teardown always cleans
both paths, so any valid mode works when building for a teardown call.
"""

from typing import Literal

from k8s.seed import operators
from k8s.seed.pipeline import Pipeline


def build(*, mode: Literal["direct", "deferred"]) -> Pipeline:
    return Pipeline([
        operators.InstallPrereqs(),
        operators.JoinCluster(mode=mode),
        operators.NodeLabel(role="worker", strict=False),
    ])
