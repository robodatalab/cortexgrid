from __future__ import annotations

import argparse
import sys
import unittest
from unittest.mock import patch

from k8s.seed import setup_node
from parameterized import parameterized  # type: ignore


def _args(**overrides) -> argparse.Namespace:
    """Shorthand for building an argparse.Namespace like parse_args would return."""
    defaults = {
        "type": "head",
        "ip": "10.0.0.1",
        "storage_path": "/mnt/hdd",
        "ssh_user": "ptrochim",
        "profile": "onprem",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestValidateAndUpdate(unittest.TestCase):
    """validate_and_update decides whether a seed is allowed and appends/updates the config."""

    def test_adds_head_entry_to_empty_config(self) -> None:
        cfg: dict = {"nodes": []}
        result = setup_node.validate_and_update(cfg, _args(type="head"))
        self.assertEqual(
            result["nodes"],
            [
                {
                    "ip": "10.0.0.1",
                    "role": "head",
                    "profile": "onprem",
                    "storage_path": "/mnt/hdd",
                }
            ],
        )

    def test_adds_worker_entry_without_storage_path(self) -> None:
        cfg = {
            "nodes": [{"ip": "10.0.0.1", "role": "head", "storage_path": "/mnt/hdd"}]
        }
        result = setup_node.validate_and_update(
            cfg, _args(type="worker", ip="10.0.0.2", storage_path=None)
        )
        self.assertEqual(
            result["nodes"][-1],
            {"ip": "10.0.0.2", "role": "worker", "profile": "onprem"},
        )

    def test_idempotent_reseed_same_ip_same_role_same_storage(self) -> None:
        cfg = {
            "nodes": [{"ip": "10.0.0.1", "role": "head", "storage_path": "/mnt/hdd"}]
        }
        result = setup_node.validate_and_update(cfg, _args(type="head"))
        self.assertEqual(
            result["nodes"],
            [
                {
                    "ip": "10.0.0.1",
                    "role": "head",
                    "profile": "onprem",
                    "storage_path": "/mnt/hdd",
                }
            ],
        )

    def test_reseed_preserves_existing_progress(self) -> None:
        """A partial checkpoint from a prior failed run must survive a re-run
        of validate_and_update, so the dispatcher can still see where it got to."""
        cfg = {
            "nodes": [
                {
                    "ip": "10.0.0.1",
                    "role": "head",
                    "storage_path": "/mnt/hdd",
                    "progress": ["InstallPrereqs", "K3sServer"],
                }
            ]
        }
        result = setup_node.validate_and_update(cfg, _args(type="head"))
        self.assertEqual(
            result["nodes"][0].get("progress"), ["InstallPrereqs", "K3sServer"]
        )

    def test_fresh_entry_has_no_progress_field(self) -> None:
        cfg: dict = {"nodes": []}
        result = setup_node.validate_and_update(cfg, _args(type="head"))
        self.assertNotIn("progress", result["nodes"][0])

    @parameterized.expand(
        [
            (
                "role_conflict",
                {"nodes": [{"ip": "10.0.0.1", "role": "worker"}]},
                {"type": "head", "storage_path": "/mnt/hdd"},
                "already registered as role=worker",
            ),
            (
                "storage_path_conflict",
                {"nodes": [{"ip": "10.0.0.1", "role": "head", "storage_path": "/old"}]},
                {"type": "head", "storage_path": "/new"},
                "storage_path=/old",
            ),
            (
                "second_head_rejected",
                {"nodes": [{"ip": "10.0.0.9", "role": "head", "storage_path": "/mnt"}]},
                {"type": "head", "ip": "10.0.0.1", "storage_path": "/mnt/hdd"},
                "already has a head at 10.0.0.9",
            ),
        ]
    )
    def test_conflicts_exit_with_message(
        self, _name: str, cfg: dict, arg_overrides: dict, expected_fragment: str
    ) -> None:
        with self.assertRaises(SystemExit) as ctx:
            setup_node.validate_and_update(cfg, _args(**arg_overrides))
        self.assertIn(expected_fragment, str(ctx.exception))


class TestParseArgs(unittest.TestCase):
    """parse_args enforces mutual exclusion on --storage-path and resolves --ssh-user."""

    def _run_parse_args(self, argv: list[str]) -> argparse.Namespace:
        with patch.object(sys, "argv", ["setup-node.py", *argv]):
            return setup_node.parse_args()

    def test_head_requires_storage_path(self) -> None:
        with patch.object(setup_node.util, "ssh_user_for_ip", return_value="u"):
            with self.assertRaises(SystemExit):
                self._run_parse_args(["--type=head", "--ip=10.0.0.1"])

    def test_worker_rejects_storage_path(self) -> None:
        with patch.object(setup_node.util, "ssh_user_for_ip", return_value="u"):
            with self.assertRaises(SystemExit):
                self._run_parse_args(
                    [
                        "--type=worker",
                        "--ip=10.0.0.1",
                        "--profile=onprem",
                        "--storage-path=/mnt/hdd",
                    ]
                )

    def test_ssh_user_resolved_from_ssh_config_by_default(self) -> None:
        with patch.object(
            setup_node.util, "ssh_user_for_ip", return_value="configured_user"
        ):
            args = self._run_parse_args(
                [
                    "--type=worker",
                    "--ip=10.0.0.1",
                    "--profile=onprem",
                ]
            )
        self.assertEqual(args.ssh_user, "configured_user")

    def test_explicit_ssh_user_overrides_ssh_config(self) -> None:
        with patch.object(
            setup_node.util, "ssh_user_for_ip", return_value="configured_user"
        ):
            args = self._run_parse_args(
                [
                    "--type=worker",
                    "--ip=10.0.0.1",
                    "--profile=onprem",
                    "--ssh-user=explicit",
                ]
            )
        self.assertEqual(args.ssh_user, "explicit")

    def test_ssh_user_falls_back_to_getpass_when_config_misses(self) -> None:
        with patch.object(setup_node.util, "ssh_user_for_ip", return_value=None):
            with patch("getpass.getuser", return_value="laptop_user"):
                args = self._run_parse_args(
                    [
                        "--type=worker",
                        "--ip=10.0.0.1",
                        "--profile=onprem",
                    ]
                )
        self.assertEqual(args.ssh_user, "laptop_user")


if __name__ == "__main__":
    unittest.main()
