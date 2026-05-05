from __future__ import annotations

import argparse
import sys
import unittest
from unittest.mock import patch

from k8s.seed import teardown_node


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


if __name__ == "__main__":
    unittest.main()
