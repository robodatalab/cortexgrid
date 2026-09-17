"""Parity test: restart_nodes orchestrates the same sequence of pipeline
setup/teardown calls that running teardown_node and setup_node by hand for
each registered node would produce.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml  # type: ignore
from parameterized import parameterized  # type: ignore

from k8s.seed import head, restart_nodes, setup_node, teardown_node, util, worker


HEAD = {
    "ip": "10.0.0.10",
    "role": "head",
    "profile": "onprem",
    "storage_path": "/mnt/hdd",
}
HEAD_WORKER = {**HEAD, "worker": True}
WORKER_1 = {"ip": "10.0.0.11", "role": "worker", "profile": "onprem"}
WORKER_2 = {"ip": "10.0.0.12", "role": "worker", "profile": "onprem"}


HEAD_ENV = (
    "GH_TOKEN=gh-token\n"
    "TAILSCALE_OPERATOR_CLIENT_ID=ts-id\n"
    "TAILSCALE_OPERATOR_CLIENT_SECRET=ts-secret\n"
    "GH_APP_ID=1\n"
    "GH_APP_INSTALLATION_ID=2\n"
    'GH_APP_PRIVATE_KEY="-----BEGIN KEY-----\nabc\n-----END KEY-----"\n'
    "ROUTE53_ACCESS_KEY_ID=r53-id\n"
    "ROUTE53_SECRET_ACCESS_KEY=r53-key\n"
)


def _seed_config(path: Path, head_entry: dict) -> None:
    with open(path, "w") as f:
        yaml.safe_dump({"nodes": [head_entry, WORKER_1, WORKER_2]}, f)


class _RecordingPipeline:
    """Stand-in for head.build() / worker.build(); records setup/teardown calls
    against a shared list so the test can compare the full sequence end-to-end.
    """

    def __init__(self, role: str, calls: list) -> None:
        self.role = role
        self.calls = calls
        self.operators = [None]  # tqdm reads len(); content is irrelevant
        self.on_step_done = None

    def setup(self, deps: dict) -> None:
        self._record("setup", deps)
        self._tick()

    def teardown(self, deps: dict) -> None:
        self._record("teardown", deps)
        self._tick()

    def _tick(self) -> None:
        if self.on_step_done is not None:
            self.on_step_done("Step")

    def _record(self, action: str, deps: dict) -> None:
        signature = {k: v for k, v in deps.items() if k != "connection"}
        if "workers" in signature:
            signature["workers"] = sorted(w["ip"] for w in signature["workers"])
        self.calls.append((action, self.role, signature))


def _worker_build_factory(calls: list):
    def _build(**_kwargs):
        return _RecordingPipeline("worker", calls)

    return _build


class RestartCallSequenceParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self._tmpdir.name) / "infra-config.yaml"
        self.env_path = Path(self._tmpdir.name) / ".env.head"
        self.env_path.write_text(HEAD_ENV)
        # The dispatchers set CORTEXGRID_HEAD_URL; restore os.environ afterwards.
        self._env_patch = mock.patch.dict(os.environ)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _capture(self, action, head_entry: dict) -> list:
        _seed_config(self.config_path, head_entry)
        calls: list = []
        secrets = [util.SECRET_K3S_TOKEN, util.SECRET_CONTROL_PLANE_IP]
        with (
            mock.patch.object(util, "CONFIG_FILE", self.config_path),
            mock.patch.object(util, "ENV_FILE", self.env_path),
            mock.patch.object(util, "connect", mock.MagicMock()),
            mock.patch.object(util, "ssh_user_for_ip", return_value="tester"),
            mock.patch.object(
                head, "build", lambda: _RecordingPipeline("head", calls)
            ),
            mock.patch.object(worker, "build", _worker_build_factory(calls)),
            mock.patch.object(
                worker,
                "build_on_head",
                lambda: _RecordingPipeline("worker_on_head", calls),
            ),
            mock.patch(
                "k8s.seed.restart_nodes.list_secrets", return_value=secrets
            ),
            mock.patch(
                "k8s.seed.restart_nodes.get_secret", side_effect=lambda k: f"<{k}>"
            ),
            mock.patch(
                "k8s.seed.setup_node.list_secrets", return_value=secrets
            ),
            mock.patch(
                "k8s.seed.setup_node.get_secret", side_effect=lambda k: f"<{k}>"
            ),
        ):
            action()
        return calls

    @parameterized.expand([("head", HEAD), ("head_worker", HEAD_WORKER)])
    def test_restart_matches_individual_invocations(
        self, _name: str, head_entry: dict
    ) -> None:
        restart_sequence = self._capture(restart_nodes.main, head_entry)
        individual_sequence = self._capture(
            lambda: _invoke_scripts_individually(head_entry), head_entry
        )
        self.assertEqual(restart_sequence, individual_sequence)


def _invoke_scripts_individually(head_entry: dict) -> None:
    _run_teardown(WORKER_1["ip"])
    _run_teardown(WORKER_2["ip"])
    _run_teardown(HEAD["ip"])
    _run_setup_head()
    if head_entry.get("worker"):
        _run_setup_worker(HEAD["ip"])
    _run_setup_worker(WORKER_1["ip"])
    _run_setup_worker(WORKER_2["ip"])


def _run_teardown(ip: str) -> None:
    with mock.patch.object(sys, "argv", ["teardown_node.py", f"--ip={ip}"]):
        teardown_node.main()


def _run_setup_head() -> None:
    with mock.patch.object(
        sys,
        "argv",
        [
            "setup_node.py",
            "--type=head",
            f"--ip={HEAD['ip']}",
            f"--profile={HEAD['profile']}",
            f"--storage-path={HEAD['storage_path']}",
        ],
    ):
        setup_node.main()


def _run_setup_worker(ip: str) -> None:
    with mock.patch.object(
        sys,
        "argv",
        [
            "setup_node.py",
            "--type=worker",
            f"--ip={ip}",
            "--profile=onprem",
        ],
    ):
        setup_node.main()


if __name__ == "__main__":
    unittest.main()
