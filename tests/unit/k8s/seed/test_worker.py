from __future__ import annotations

from typing import Literal
import unittest
from unittest import mock

from k8s.seed import util, worker
from k8s.seed.operators.node_label import NodeLabel


def _node_label(mode: Literal["direct", "deferred"]) -> NodeLabel:
    operators = worker.build(mode=mode).operators
    return next(op for op in operators if isinstance(op, NodeLabel))


class TestWorkerNodeLabel(unittest.TestCase):
    """A direct join that never registers fails setup; a deferred one, which
    cannot have registered yet, does not."""

    def test_direct_join_fails_when_the_node_never_registers(self) -> None:
        with (
            mock.patch.object(util, "resolve_node_name", return_value=None),
            mock.patch.object(util, "kubectl") as kubectl,
            self.assertRaises(SystemExit),
        ):
            _node_label("direct").setup({"node_ip": "100.80.27.32"})

        kubectl.assert_not_called()

    def test_deferred_join_skips_a_node_that_has_not_registered(self) -> None:
        with (
            mock.patch.object(util, "resolve_node_name", return_value=None),
            mock.patch.object(util, "kubectl") as kubectl,
        ):
            _node_label("deferred").setup({"node_ip": "100.80.27.32"})

        kubectl.assert_not_called()

    def test_direct_join_labels_the_registered_node(self) -> None:
        with (
            mock.patch.object(util, "resolve_node_name", return_value="spark-40f4"),
            mock.patch.object(util, "kubectl") as kubectl,
        ):
            _node_label("direct").setup({"node_ip": "100.80.27.32"})

        kubectl.assert_called_once_with(
            "label", "node", "spark-40f4", "role=worker", "--overwrite", capture=False
        )


if __name__ == "__main__":
    unittest.main()
