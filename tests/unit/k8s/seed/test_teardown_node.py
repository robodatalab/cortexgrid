from __future__ import annotations

import argparse
import copy
import sys
import unittest
from unittest import mock
from unittest.mock import patch

from k8s.seed import teardown_node


HEAD_WORKER = {
    "ip": "10.0.0.1",
    "role": "head",
    "profile": "onprem",
    "storage_path": "/mnt/hdd",
    "worker": True,
}


class TestParseArgs(unittest.TestCase):
    """parse_args resolves --ssh-user from ~/.ssh/config when not supplied."""

    def _run(self, argv: list[str]) -> argparse.Namespace:
        with patch.object(sys, "argv", ["teardown-node.py", *argv]):
            return teardown_node.parse_args()

    def test_ssh_user_resolved_from_config(self) -> None:
        with patch.object(
            teardown_node.util, "ssh_user_for_ip", return_value="cfg_user"
        ):
            args = self._run(["--ip=10.0.0.1"])
        self.assertEqual(args.ssh_user, "cfg_user")

    def test_explicit_ssh_user_overrides(self) -> None:
        with patch.object(
            teardown_node.util, "ssh_user_for_ip", return_value="cfg_user"
        ):
            args = self._run(["--ip=10.0.0.1", "--ssh-user=explicit"])
        self.assertEqual(args.ssh_user, "explicit")

    def test_ssh_user_falls_back_to_getpass(self) -> None:
        with patch.object(teardown_node.util, "ssh_user_for_ip", return_value=None):
            with patch("getpass.getuser", return_value="laptop_user"):
                args = self._run(["--ip=10.0.0.1"])
        self.assertEqual(args.ssh_user, "laptop_user")


class TestMainRefusesUntrackedIp(unittest.TestCase):
    """main() exits early if the IP isn't in infra-config.yaml — won't touch untracked nodes."""

    def test_exits_when_ip_missing_from_config(self) -> None:
        with patch.object(
            teardown_node.util, "load_config", return_value={"nodes": []}
        ):
            with patch.object(
                teardown_node,
                "parse_args",
                return_value=argparse.Namespace(ip="10.0.0.99", ssh_user="u"),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    teardown_node.main()
        self.assertIn("not in infra-config.yaml", str(ctx.exception))

    def test_does_not_prompt_or_ssh_when_ip_missing(self) -> None:
        """Early exit should happen before any input() or SSH attempt."""
        with patch.object(
            teardown_node.util, "load_config", return_value={"nodes": []}
        ):
            with patch.object(
                teardown_node,
                "parse_args",
                return_value=argparse.Namespace(ip="10.0.0.99", ssh_user="u"),
            ):
                with patch("builtins.input") as mock_input:
                    with patch("getpass.getpass") as mock_getpass:
                        with patch.object(
                            teardown_node.util, "connect"
                        ) as mock_connect:
                            with self.assertRaises(SystemExit):
                                teardown_node.main()
        mock_input.assert_not_called()
        mock_getpass.assert_not_called()
        mock_connect.assert_not_called()


class TestWorkerTeardown(unittest.TestCase):
    """--worker on the head removes only the worker role; a full head teardown
    removes it before tearing the head down."""

    def _main(self, entry: dict, worker: bool) -> tuple[mock.MagicMock, mock.MagicMock, list]:
        cfg = {"nodes": [entry]}
        saved: list = []
        head_pipeline = mock.MagicMock(operators=[None])
        worker_on_head_pipeline = mock.MagicMock(operators=[None])
        with (
            patch.object(teardown_node.util, "load_config", side_effect=lambda: copy.deepcopy(cfg)),
            patch.object(teardown_node.util, "save_config", side_effect=saved.append),
            patch.object(teardown_node.util, "checkpoint_step_done"),
            patch.object(teardown_node.util, "connect"),
            patch.object(teardown_node.head, "build", return_value=head_pipeline),
            patch.object(teardown_node.worker, "build_on_head", return_value=worker_on_head_pipeline),
            patch.object(
                teardown_node,
                "parse_args",
                return_value=argparse.Namespace(ip=entry["ip"], ssh_user="u", worker=worker),
            ),
        ):
            teardown_node.main()
        return head_pipeline, worker_on_head_pipeline, saved

    def test_worker_flag_on_head_keeps_the_head(self) -> None:
        head_pipeline, worker_on_head_pipeline, saved = self._main(HEAD_WORKER, worker=True)

        worker_on_head_pipeline.teardown.assert_called_once_with({"node_ip": HEAD_WORKER["ip"]})
        head_pipeline.teardown.assert_not_called()
        self.assertEqual(saved[-1]["nodes"], [{k: v for k, v in HEAD_WORKER.items() if k != "worker"}])

    def test_worker_flag_on_head_that_is_not_a_worker_exits(self) -> None:
        head = {k: v for k, v in HEAD_WORKER.items() if k != "worker"}
        with self.assertRaises(SystemExit) as ctx:
            self._main(head, worker=True)
        self.assertIn("is not a worker", str(ctx.exception))

    def test_full_head_teardown_removes_the_worker_role_first(self) -> None:
        head_pipeline, worker_on_head_pipeline, saved = self._main(HEAD_WORKER, worker=False)

        worker_on_head_pipeline.teardown.assert_called_once()
        head_pipeline.teardown.assert_called_once()
        self.assertEqual(saved[-1]["nodes"], [])


if __name__ == "__main__":
    unittest.main()
