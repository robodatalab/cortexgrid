from __future__ import annotations

import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from k8s.seed import util
from k8s.seed.operators.join_cluster import JoinCluster
from k8s.seed.operators.k3s_server import K3sServer
from k8s.seed.scripts import robolab_join


class TestK3sVersionPin(unittest.TestCase):
    """The head and every worker install the same pinned k3s, whichever path
    installs it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.c = mock.MagicMock(host="node")
        self.c.run.return_value.stdout = "active\n"
        self.scripts: list[str] = []  # every sudo_script, in order
        self.files: dict[str, str] = {}  # write_remote_file: path -> content

        def sudo_script(_c: object, script: str, **_kwargs: object) -> None:
            self.scripts.append(script)

        def write_remote_file(
            _c: object, content: str, path: str, **_kwargs: object
        ) -> None:
            self.files[path] = content

        for name, fake in (
            ("sudo_script", sudo_script),
            ("write_remote_file", write_remote_file),
            ("bind_k3s_to_tailscale", mock.MagicMock()),
        ):
            p = mock.patch.object(util, name, fake)
            p.start()
            self.addCleanup(p.stop)

    def test_head_install_is_pinned(self) -> None:
        bootstrap = self.tmp / "argocd.yaml"
        bootstrap.write_text("")
        with mock.patch.object(K3sServer, "_await_bootstrap_applied"):
            K3sServer().setup({
                "connection": self.c, "bootstrap_file": bootstrap,
                "node_ip": "100.110.47.88", "profile": "onprem",
            })

        self.assertIn(
            f"INSTALL_K3S_VERSION={shlex.quote(util.K3S_VERSION)}", self.scripts[0]
        )

    def test_direct_join_install_is_pinned(self) -> None:
        JoinCluster(mode="direct").setup({
            "connection": self.c, "node_ip": "100.80.27.32",
            "head_ip": "100.110.47.88", "head_token": "token",
        })

        install = next(s for s in self.scripts if "get.k3s.io" in s)
        self.assertIn(f"INSTALL_K3S_VERSION={shlex.quote(util.K3S_VERSION)}", install)

    def test_deferred_join_installs_the_pinned_version_when_the_timer_fires(
        self,
    ) -> None:
        JoinCluster(mode="deferred").setup({
            "connection": self.c, "node_ip": "100.80.27.32",
            "head_url": "http://robolab-head:7700",
        })
        # Run the timer script against the env file the operator wrote.
        env_file = self.tmp / "robolab-bootstrap"
        env_file.write_text(self.files[util.JOIN_ENV_PATH])
        secrets = {
            robolab_join.K3S_TOKEN_SECRET: "token",
            robolab_join.CONTROL_PLANE_IP_SECRET: "100.110.47.88",
        }
        with (
            mock.patch.object(robolab_join, "ENV_PATH", str(env_file)),
            mock.patch.object(
                robolab_join, "_get_secret", side_effect=lambda _url, name: secrets[name]
            ),
            mock.patch.object(robolab_join, "_self_remove"),
            mock.patch.object(robolab_join.subprocess, "run") as run,
            mock.patch.dict(robolab_join.os.environ, {}, clear=True),
        ):
            robolab_join.main()

        install = run.call_args.args[0]
        self.assertIn(f"INSTALL_K3S_VERSION='{util.K3S_VERSION}'", install)
        self.assertIn("K3S_URL='https://100.110.47.88:6443'", install)


if __name__ == "__main__":
    unittest.main()
