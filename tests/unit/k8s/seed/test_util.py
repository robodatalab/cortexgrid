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


class TestPollUntil(unittest.TestCase):
    """poll_until surfaces a persistent probe failure as TimeoutError with
    the last error message — instead of spinning silently forever."""

    def test_returns_when_check_eventually_ok(self) -> None:
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return (calls["n"] >= 2, "not yet")

        with patch("time.sleep"):
            util.poll_until(check, "thing", timeout_s=10, poll_s=0.01)
        self.assertEqual(calls["n"], 2)

    def test_raises_timeout_with_last_error(self) -> None:
        def check():
            return False, "TLS: bad cert"

        with patch("time.sleep"):
            with self.assertRaises(TimeoutError) as ctx:
                util.poll_until(check, "argocd", timeout_s=0.01, poll_s=0.01)
        self.assertIn("argocd", str(ctx.exception))
        self.assertIn("TLS: bad cert", str(ctx.exception))


class TestResolveNodeNameKubectlBroken(unittest.TestCase):
    """resolve_node_name distinguishes 'kubectl broken' from 'node not there'.
    With wait_for>0, a consistent kubectl failure must raise, not return None."""

    def test_raises_when_kubectl_never_succeeded_and_waited(self) -> None:
        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = ""
        fail.stderr = "x509: certificate invalid"
        with patch("subprocess.run", return_value=fail):
            with patch("time.sleep"):
                with self.assertRaises(RuntimeError) as ctx:
                    util.resolve_node_name("10.0.0.99", wait_for=0.01)
        self.assertIn("kubectl", str(ctx.exception))
        self.assertIn("x509", str(ctx.exception))


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



class TestHeadUrl(unittest.TestCase):
    """head_url points at the registered head, or its tailnet name before one exists."""

    def test_uses_registered_head_ip(self) -> None:
        cfg = {"nodes": [{"ip": "10.0.0.2", "role": "worker"}, {"ip": "10.0.0.1", "role": "head"}]}
        self.assertEqual(util.head_url(cfg), "http://10.0.0.1:7700")

    def test_falls_back_to_tailnet_hostname(self) -> None:
        cfg = {"nodes": [{"ip": "10.0.0.2", "role": "worker"}]}
        self.assertEqual(util.head_url(cfg), "http://robolab-head:7700")


class TestLoadHeadEnv(unittest.TestCase):
    """load_head_env reads .env.head and exits naming the keys the profile needs but lacks."""

    _COMMON = (
        "GH_TOKEN=t\n"
        "TAILSCALE_OPERATOR_CLIENT_ID=i\n"
        "TAILSCALE_OPERATOR_CLIENT_SECRET=s\n"
        "GH_APP_ID=1\n"
        "GH_APP_INSTALLATION_ID=2\n"
        'GH_APP_PRIVATE_KEY="-----BEGIN KEY-----\nabc\n-----END KEY-----"\n'
    )

    def _load(self, content: str, profile: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".env.head"
            path.write_text(content)
            with patch.object(util, "ENV_FILE", path):
                return util.load_head_env(profile)

    def test_aws_does_not_need_route53_keys(self) -> None:
        env = self._load(self._COMMON, "aws")
        self.assertEqual(env["GH_APP_PRIVATE_KEY"], "-----BEGIN KEY-----\nabc\n-----END KEY-----")

    def test_onprem_requires_route53_keys(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._load(self._COMMON + "ROUTE53_ACCESS_KEY_ID=\n", "onprem")
        self.assertIn("ROUTE53_ACCESS_KEY_ID, ROUTE53_SECRET_ACCESS_KEY", str(ctx.exception))

    def test_onprem_with_route53_keys_passes(self) -> None:
        env = self._load(
            self._COMMON + "ROUTE53_ACCESS_KEY_ID=a\nROUTE53_SECRET_ACCESS_KEY=b\n", "onprem"
        )
        self.assertEqual(env["ROUTE53_ACCESS_KEY_ID"], "a")

    def test_missing_file_lists_every_required_key(self) -> None:
        with patch.object(util, "ENV_FILE", Path("/nonexistent/.env.head")):
            with self.assertRaises(SystemExit) as ctx:
                util.load_head_env("aws")
        self.assertIn("GH_TOKEN", str(ctx.exception))
        self.assertIn("GH_APP_PRIVATE_KEY", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
