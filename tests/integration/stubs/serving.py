"""Test fixture for the model_serving integration test.

Defines a `cortexflow.Model` whose only state is an integer constant; the
inference method just adds the constant to its argument. Same class works
locally and on the Ray Serve cluster.

Also exports `contact_deployment`, which exercises the deploy-then-call path
end-to-end. Defined at module level so it can be picked up by
`cortexflow.remote()` and run on a Ray worker.
"""

from __future__ import annotations

import json
from pathlib import Path

import cortexflow


class AddConstantModel(cortexflow.Model):
    """A facsimile model. Trained 'weights' = one integer; inference returns
    `x + constant`. Used by the integration tests to exercise the save +
    deploy + call cycle without needing real ML deps."""

    def __init__(self, constant: int) -> None:
        self.constant = constant

    def infer(self, x: int) -> int:
        return x + self.constant

    def save(self, d: Path) -> None:
        (d / "weights.json").write_text(json.dumps({"constant": self.constant}))

    @classmethod
    def load(cls, d: Path) -> "AddConstantModel":
        constant = json.loads((d / "weights.json").read_text())["constant"]
        return cls(constant=constant)


def contact_deployment(family: str, suffix: str, run_name: str, x: int, expected: int) -> None:
    """Re-resolve a deployment by triple, call infer(x), assert the answer.

    Lives at module level so cortexflow.remote() can ship it to a Ray worker."""
    deployed = cortexflow.deploy_model(family, suffix, run_name)
    result = deployed.infer(x)
    assert result == expected, (result, expected)
