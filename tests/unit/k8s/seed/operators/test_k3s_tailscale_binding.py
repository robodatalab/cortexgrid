from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from k8s.seed import util
from k8s.seed.operators.join_cluster import JoinCluster
from k8s.seed.operators.k3s_server import K3sServer


class TestK3sServerTailscaleBinding(unittest.TestCase):
    """The head's k3s is bound to tailscaled before it is installed, and
    unbound on teardown."""

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        bootstrap = tmp / "argocd.yaml"
        bootstrap.write_text("")
        self.deps = {
            "connection": mock.MagicMock(host="head"),
            "bootstrap_file": bootstrap,
            "node_ip": "100.110.47.88",
            "profile": "onprem",
        }
        self.calls = mock.MagicMock()
        for name in ("sudo_script", "wipe_k3s_residue",
                     "bind_k3s_to_tailscale", "unbind_k3s_from_tailscale"):
            p = mock.patch.object(util, name, getattr(self.calls, name))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(K3sServer, "_await_bootstrap_applied")
        p.start()
        self.addCleanup(p.stop)

    def test_setup_binds_k3s_to_tailscale_before_installing(self) -> None:
        K3sServer().setup(self.deps)

        names = [call[0] for call in self.calls.mock_calls]
        self.assertEqual(names[:2], ["bind_k3s_to_tailscale", "sudo_script"])
        self.calls.bind_k3s_to_tailscale.assert_called_once_with(
            self.deps["connection"], util.K3S_SERVER_UNIT
        )

    def test_teardown_unbinds_k3s_from_tailscale(self) -> None:
        K3sServer().teardown(self.deps)

        self.calls.unbind_k3s_from_tailscale.assert_called_once_with(
            self.deps["connection"], util.K3S_SERVER_UNIT
        )


class TestJoinClusterTailscaleBinding(unittest.TestCase):
    """A worker's k3s agent is bound to tailscaled in both join modes, before
    the join path runs, and unbound on teardown."""

    def setUp(self) -> None:
        self.c = mock.MagicMock(host="worker")
        self.calls = mock.MagicMock()
        for name in ("sudo_script", "wipe_k3s_residue",
                     "bind_k3s_to_tailscale", "unbind_k3s_from_tailscale"):
            p = mock.patch.object(util, name, getattr(self.calls, name))
            p.start()
            self.addCleanup(p.stop)
        for method in ("_direct_join", "_deferred_join"):
            p = mock.patch.object(JoinCluster, method, getattr(self.calls, method))
            p.start()
            self.addCleanup(p.stop)

    def _assert_bound_before(self, join_method: str) -> None:
        names = [call[0] for call in self.calls.mock_calls]
        self.assertEqual(names, ["bind_k3s_to_tailscale", join_method])
        self.calls.bind_k3s_to_tailscale.assert_called_once_with(
            self.c, util.K3S_AGENT_UNIT
        )

    def test_direct_join_binds_the_agent_to_tailscale_first(self) -> None:
        JoinCluster(mode="direct").setup({
            "connection": self.c, "node_ip": "100.80.27.32",
            "head_ip": "100.110.47.88", "head_token": "token",
        })

        self._assert_bound_before("_direct_join")

    def test_deferred_join_binds_the_agent_to_tailscale_first(self) -> None:
        JoinCluster(mode="deferred").setup({
            "connection": self.c, "node_ip": "100.80.27.32",
            "head_url": "http://robolab-head:7700",
        })

        self._assert_bound_before("_deferred_join")

    def test_teardown_unbinds_the_agent_from_tailscale(self) -> None:
        JoinCluster(mode="direct").teardown({"connection": self.c})

        self.calls.unbind_k3s_from_tailscale.assert_called_once_with(
            self.c, util.K3S_AGENT_UNIT
        )


if __name__ == "__main__":
    unittest.main()
