from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


from k8s.seed import util
from parameterized import parameterized  # type: ignore


class TestLoadSaveConfig(unittest.TestCase):
    """load_config / save_config round-trip the infra topology via YAML."""

    def test_load_config_returns_empty_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.object(util, "CONFIG_FILE", Path(td) / "nope.yaml"):
                self.assertEqual(util.load_config(), {"nodes": []})

    def test_save_then_load_roundtrips(self) -> None:
        cfg = {
            "nodes": [
                {"ip": "10.0.0.1", "role": "head", "storage_path": "/mnt/hdd"},
                {"ip": "10.0.0.2", "role": "worker"},
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            with patch.object(util, "CONFIG_FILE", Path(td) / "infra.yaml"):
                util.save_config(cfg)
                self.assertEqual(util.load_config(), cfg)

    def test_load_config_empty_file_returns_empty_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "empty.yaml"
            path.write_text("")
            with patch.object(util, "CONFIG_FILE", path):
                self.assertEqual(util.load_config(), {"nodes": []})


class TestSshUserForIp(unittest.TestCase):
    """ssh_user_for_ip scans ~/.ssh/config and matches HostName entries to users."""

    def _write_ssh_config(self, content: str) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / ".ssh" / "config"
        path.parent.mkdir()
        path.write_text(content)
        return Path(td.name)

    def test_finds_user_by_matching_hostname(self) -> None:
        home = self._write_ssh_config(
            "Host p5\n"
            "    HostName 100.110.47.89\n"
            "    User ptrochim\n"
            "\n"
            "Host dgx\n"
            "    HostName 100.80.27.32\n"
            "    User root\n"
        )
        with patch.object(util.Path, "home", return_value=home):
            self.assertEqual(util.ssh_user_for_ip("100.110.47.89"), "ptrochim")
            self.assertEqual(util.ssh_user_for_ip("100.80.27.32"), "root")

    def test_returns_none_when_ip_not_in_config(self) -> None:
        home = self._write_ssh_config(
            "Host p5\n    HostName 100.110.47.89\n    User ptrochim\n"
        )
        with patch.object(util.Path, "home", return_value=home):
            self.assertIsNone(util.ssh_user_for_ip("10.0.0.99"))

    def test_returns_none_when_ssh_config_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.object(util.Path, "home", return_value=Path(td)):
                self.assertIsNone(util.ssh_user_for_ip("100.110.47.89"))


class TestResolveNodeName(unittest.TestCase):
    """resolve_node_name wraps `kubectl get nodes -o json` and filters by IP."""

    def _fake_kubectl_result(self, nodes: list[dict]) -> MagicMock:
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"items": nodes})
        return result

    def test_returns_name_when_ip_matches(self) -> None:
        nodes = [
            {
                "metadata": {"name": "p5-head"},
                "status": {"addresses": [{"address": "100.110.47.89"}]},
            }
        ]
        with patch("subprocess.run", return_value=self._fake_kubectl_result(nodes)):
            self.assertEqual(util.resolve_node_name("100.110.47.89"), "p5-head")

    def test_returns_none_when_no_match_and_no_wait(self) -> None:
        with patch("subprocess.run", return_value=self._fake_kubectl_result([])):
            self.assertIsNone(util.resolve_node_name("10.0.0.99"))

    def test_returns_none_when_kubectl_unreachable(self) -> None:
        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = ""
        with patch("subprocess.run", return_value=fail):
            self.assertIsNone(util.resolve_node_name("100.110.47.89"))

    @parameterized.expand(
        [
            ("matches_on_first_address", "10.0.0.1"),
            ("matches_on_second_address", "10.0.0.2"),
        ]
    )
    def test_matches_any_status_address(self, _name: str, ip: str) -> None:
        nodes = [
            {
                "metadata": {"name": "the-node"},
                "status": {
                    "addresses": [
                        {"address": "10.0.0.1"},
                        {"address": "10.0.0.2"},
                    ]
                },
            }
        ]
        with patch("subprocess.run", return_value=self._fake_kubectl_result(nodes)):
            self.assertEqual(util.resolve_node_name(ip), "the-node")


class TestCheckpointStepDone(unittest.TestCase):
    """checkpoint_step_done tracks pipeline progress on the node entry.

    Setup appends; teardown pops matching. The field is removed when empty."""

    def _with_temp_config(self, cfg: dict):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "infra.yaml"
        patcher = patch.object(util, "CONFIG_FILE", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        util.save_config(cfg)
        return path

    def test_setup_appends_operator_name(self) -> None:
        self._with_temp_config({"nodes": [{"ip": "10.0.0.1", "role": "head"}]})
        util.checkpoint_step_done("10.0.0.1", "InstallPrereqs", "setup")
        util.checkpoint_step_done("10.0.0.1", "K3sServer", "setup")
        self.assertEqual(
            util.load_config()["nodes"][0]["progress"],
            ["InstallPrereqs", "K3sServer"],
        )

    def test_teardown_pops_matching_last_entry(self) -> None:
        self._with_temp_config(
            {
                "nodes": [
                    {
                        "ip": "10.0.0.1",
                        "role": "head",
                        "progress": ["InstallPrereqs", "K3sServer"],
                    }
                ]
            }
        )
        util.checkpoint_step_done("10.0.0.1", "K3sServer", "teardown")
        self.assertEqual(util.load_config()["nodes"][0]["progress"], ["InstallPrereqs"])

    def test_teardown_ignores_non_matching_last_entry(self) -> None:
        """Teardown of an operator whose setup never completed is a no-op.
        Keeps the checkpoint honest when teardown is called on a partial setup."""
        self._with_temp_config(
            {
                "nodes": [
                    {"ip": "10.0.0.1", "role": "head", "progress": ["InstallPrereqs"]}
                ]
            }
        )
        util.checkpoint_step_done("10.0.0.1", "K3sServer", "teardown")
        self.assertEqual(util.load_config()["nodes"][0]["progress"], ["InstallPrereqs"])

    def test_progress_field_removed_when_list_empties(self) -> None:
        self._with_temp_config(
            {
                "nodes": [
                    {"ip": "10.0.0.1", "role": "head", "progress": ["InstallPrereqs"]}
                ]
            }
        )
        util.checkpoint_step_done("10.0.0.1", "InstallPrereqs", "teardown")
        self.assertNotIn("progress", util.load_config()["nodes"][0])

    def test_invalid_direction_raises(self) -> None:
        self._with_temp_config({"nodes": [{"ip": "10.0.0.1", "role": "head"}]})
        with self.assertRaises(ValueError):
            util.checkpoint_step_done("10.0.0.1", "InstallPrereqs", "sideways")

    def test_unknown_ip_is_silent_noop(self) -> None:
        """Writing a checkpoint for an IP not in the config is tolerated —
        the dispatcher may have just removed the entry (end-of-teardown)."""
        self._with_temp_config({"nodes": [{"ip": "10.0.0.1", "role": "head"}]})
        util.checkpoint_step_done("10.0.0.99", "InstallPrereqs", "setup")
        self.assertEqual(util.load_config()["nodes"], [{"ip": "10.0.0.1", "role": "head"}])


if __name__ == "__main__":
    unittest.main()
