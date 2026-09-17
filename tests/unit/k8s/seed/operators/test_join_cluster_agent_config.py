from __future__ import annotations

import re
import unittest
from unittest import mock

import yaml  # type: ignore
from parameterized import parameterized  # type: ignore

from k8s.seed import util
from k8s.seed.operators.join_cluster import JoinCluster


_CONFIG_HEREDOC = re.compile(
    r"cat > /etc/rancher/k3s/config\.yaml <<EOF\n(.*?)^EOF$", re.S | re.M
)


class TestAgentConfig(unittest.TestCase):
    """Both join modes write the same agent config, and it labels the node
    role=worker + worker=true (+ gpu=true on a GPU host) at registration."""

    def setUp(self) -> None:
        self.scripts: list[str] = []

        def sudo_script(_c: object, script: str, **_kwargs: object) -> None:
            self.scripts.append(script)

        for name, fake in (
            ("sudo_script", sudo_script),
            ("write_remote_file", mock.MagicMock()),
            ("bind_k3s_to_tailscale", mock.MagicMock()),
            ("has_gpu", lambda _c: self.gpu),
        ):
            p = mock.patch.object(util, name, fake)
            p.start()
            self.addCleanup(p.stop)
        self.c = mock.MagicMock(host="worker")
        self.c.run.return_value.stdout = "active\n"

    def _written_config(self) -> dict:
        configs = [m.group(1) for s in self.scripts for m in _CONFIG_HEREDOC.finditer(s)]
        self.assertEqual(len(configs), 1)
        return yaml.safe_load(configs[0])

    @parameterized.expand([
        (mode, deps, gpu, labels)
        for mode, deps in (
            ("direct", {"head_ip": "100.110.47.88", "head_token": "token"}),
            ("deferred", {"head_url": "http://robolab-head:7700"}),
        )
        for gpu, labels in (
            (True, ["role=worker", "worker=true", "gpu=true"]),
            (False, ["role=worker", "worker=true"]),
        )
    ])
    def test_agent_registers_labelled_for_its_compute(
        self, mode: str, deps: dict, gpu: bool, labels: list[str]
    ) -> None:
        self.gpu = gpu
        JoinCluster(mode=mode).setup(
            {"connection": self.c, "node_ip": "100.80.27.32", **deps}
        )

        self.assertEqual(
            self._written_config(),
            {
                "node-ip": "100.80.27.32",
                "flannel-iface": "tailscale0",
                "node-label": labels,
            },
        )


if __name__ == "__main__":
    unittest.main()
