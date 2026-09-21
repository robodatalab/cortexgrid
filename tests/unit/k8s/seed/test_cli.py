from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml  # type: ignore
from parameterized import parameterized  # type: ignore

from k8s.seed import aws, cli, head, util, worker


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

HEAD = {"ip": "10.0.0.1", "profile": "onprem", "storage": "/mnt/hdd", "done": ["ArgoReady"]}
ON_HEAD = {"ip": "10.0.0.1", "done": ["ComputeLabels"]}
DGX = {"ip": "10.0.0.2", "done": ["JoinCluster"]}


def _cfg(head_entry: dict | None = None, **workers: dict) -> dict:
    cfg: dict = {"workers": {alias: dict(w) for alias, w in workers.items()}}
    if head_entry is not None:
        cfg["head"] = dict(head_entry)
    return cfg


class TestMain(unittest.TestCase):
    """main() maps each command line onto one command and rejects malformed ones."""

    @parameterized.expand([
        ("onprem_head", ["add", "head", "--onprem", "--storage=/mnt", "10.0.0.1"],
         "add_head", ("10.0.0.1", "onprem", "/mnt", None)),
        ("aws_head", ["add", "head", "--aws", "--ssh-user=u"], "add_aws_head", ("u",)),
        ("worker", ["add", "worker", "--alias=dgx", "10.0.0.2"],
         "add_worker", ("dgx", "10.0.0.2", None)),
        ("del_worker", ["del", "worker", "dgx"], "del_worker", ("dgx", None)),
        ("del_head", ["del", "head", "--ssh-user=u"], "del_head", ("u",)),
        ("del_all", ["del", "--all"], "del_all", ()),
        ("restart", ["restart"], "restart", ()),
    ])
    def test_dispatches(self, _name: str, argv: list[str], command: str, args: tuple) -> None:
        with mock.patch.object(cli, command) as called:
            cli.main(argv)
        called.assert_called_once_with(*args)

    @parameterized.expand([
        ("onprem_without_storage", ["add", "head", "--onprem", "10.0.0.1"]),
        ("onprem_without_ip", ["add", "head", "--onprem", "--storage=/mnt"]),
        ("aws_with_ip", ["add", "head", "--aws", "10.0.0.1"]),
        ("aws_with_storage", ["add", "head", "--aws", "--storage=/mnt"]),
        ("no_profile", ["add", "head", "--storage=/mnt", "10.0.0.1"]),
        ("both_profiles", ["add", "head", "--onprem", "--aws"]),
        ("worker_without_alias", ["add", "worker", "10.0.0.2"]),
        ("del_nothing", ["del"]),
        ("del_all_and_role", ["del", "--all", "head"]),
    ])
    def test_rejects(self, _name: str, argv: list[str]) -> None:
        with (
            mock.patch.multiple(
                cli,
                add_head=mock.DEFAULT, add_aws_head=mock.DEFAULT, add_worker=mock.DEFAULT,
                del_head=mock.DEFAULT, del_worker=mock.DEFAULT, del_all=mock.DEFAULT,
            ),
            mock.patch("sys.stderr"),
            self.assertRaises(SystemExit),
        ):
            cli.main(argv)


class TestValidatedHead(unittest.TestCase):
    def test_fresh_head(self) -> None:
        self.assertEqual(
            cli.validated_head(_cfg(), "10.0.0.1", "onprem", "/mnt/hdd"),
            {"ip": "10.0.0.1", "profile": "onprem", "storage": "/mnt/hdd", "done": []},
        )

    def test_readding_keeps_the_steps_done(self) -> None:
        self.assertEqual(
            cli.validated_head(_cfg(HEAD), "10.0.0.1", "onprem", "/mnt/hdd"), HEAD
        )

    @parameterized.expand([
        ("second_head", _cfg(HEAD), ("10.0.0.9", "onprem", "/mnt/hdd"),
         "already has a head at 10.0.0.1"),
        ("storage_change", _cfg(HEAD), ("10.0.0.1", "onprem", "/other"),
         "--storage=/mnt/hdd"),
        ("profile_change", _cfg(HEAD), ("10.0.0.1", "aws", "/mnt/hdd"),
         "--onprem head at 10.0.0.1"),
        ("host_is_a_worker", _cfg(dgx=DGX), ("10.0.0.2", "onprem", "/mnt/hdd"),
         "./cg del worker dgx"),
    ])
    def test_conflicts_exit(self, _name: str, cfg: dict, args: tuple, fragment: str) -> None:
        with self.assertRaises(SystemExit) as ctx:
            cli.validated_head(cfg, *args)
        self.assertIn(fragment, str(ctx.exception))


class TestValidatedWorker(unittest.TestCase):
    def test_fresh_worker(self) -> None:
        self.assertEqual(
            cli.validated_worker(_cfg(HEAD), "dgx", "10.0.0.2"), {"ip": "10.0.0.2", "done": []}
        )

    def test_worker_on_the_heads_ip(self) -> None:
        self.assertEqual(
            cli.validated_worker(_cfg(HEAD), "p5", "10.0.0.1"), {"ip": "10.0.0.1", "done": []}
        )

    def test_readding_keeps_the_steps_done(self) -> None:
        self.assertEqual(cli.validated_worker(_cfg(HEAD, dgx=DGX), "dgx", "10.0.0.2"), DGX)

    @parameterized.expand([
        ("alias_elsewhere", ("dgx", "10.0.0.3"), "worker dgx is at 10.0.0.2"),
        ("ip_taken", ("spark", "10.0.0.2"), "already worker dgx"),
    ])
    def test_conflicts_exit(self, _name: str, args: tuple, fragment: str) -> None:
        with self.assertRaises(SystemExit) as ctx:
            cli.validated_worker(_cfg(HEAD, dgx=DGX), *args)
        self.assertIn(fragment, str(ctx.exception))


class _RecordingPipeline:
    """Stand-in for head.build() / worker.build(): records each setup and
    teardown with its deps, and reports one step done, named after the role."""

    def __init__(self, role: str, calls: list) -> None:
        self.role = role
        self.calls = calls
        self.on_step_done = None

    def steps(self, deps: dict) -> list:
        return [None]

    def setup(self, deps: dict) -> None:
        self._record("setup", deps)

    def teardown(self, deps: dict) -> None:
        self._record("teardown", deps)

    def _record(self, action: str, deps: dict) -> None:
        signature = {k: v for k, v in deps.items() if k != "connection"}
        if "workers" in signature:
            signature["workers"] = sorted(w["ip"] for w in signature["workers"])
        self.calls.append((action, self.role, deps.get("node_ip"), signature))
        if self.on_step_done is not None:
            self.on_step_done(self.role)


class _ClusterTest(unittest.TestCase):
    """Runs commands against a temporary infra-config.yaml and .env.head, with
    every pipeline, SSH connection, secret lookup and terraform call faked."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "infra-config.yaml"
        env_path = Path(tmp.name) / ".env.head"
        env_path.write_text(HEAD_ENV)
        self.calls: list = []
        self.connect = mock.MagicMock()
        secrets = [util.SECRET_K3S_TOKEN, util.SECRET_CONTROL_PLANE_IP]
        for patcher in [
            mock.patch.dict(os.environ),
            mock.patch.object(util, "CONFIG_FILE", self.config_path),
            mock.patch.object(util, "ENV_FILE", env_path),
            mock.patch.object(util, "connect", self.connect),
            mock.patch.object(util, "ssh_user_for_ip", return_value="tester"),
            mock.patch.object(head, "build", lambda: _RecordingPipeline("head", self.calls)),
            mock.patch.object(
                worker, "build",
                lambda mode: _RecordingPipeline(f"worker:{mode}", self.calls),
            ),
            mock.patch.object(
                worker, "build_on_head",
                lambda: _RecordingPipeline("worker_on_head", self.calls),
            ),
            mock.patch.object(cli, "list_secrets", return_value=secrets),
            mock.patch.object(cli, "get_secret", side_effect=lambda k: f"<{k}>"),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.apply = self._patch(aws, "apply", return_value="10.0.0.7")
        self.destroy = self._patch(aws, "destroy")

    def _patch(self, target, name: str, **kwargs) -> mock.MagicMock:
        patcher = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def given(self, cfg: dict) -> None:
        util.save_config(cfg)

    def config(self) -> dict:
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def sequence(self) -> list[tuple[str, str, str]]:
        return [(action, role, ip) for action, role, ip, _ in self.calls]


class TestAdd(_ClusterTest):
    def test_add_head_records_it_with_its_steps(self) -> None:
        cli.add_head("10.0.0.1", "onprem", "/mnt/hdd", None)

        self.assertEqual(self.sequence(), [("setup", "head", "10.0.0.1")])
        self.assertEqual(
            self.config()["head"],
            {"ip": "10.0.0.1", "profile": "onprem", "storage": "/mnt/hdd", "done": ["head"]},
        )
        self.assertEqual(self.calls[0][3]["github_token"], "gh-token")

    def test_head_setup_labels_joined_workers_not_its_own_worker_role(self) -> None:
        self.given(_cfg(HEAD, p5=ON_HEAD, dgx=DGX))
        cli.add_head("10.0.0.1", "onprem", "/mnt/hdd", None)

        self.assertEqual(self.calls[0][3]["workers"], ["10.0.0.2"])

    def test_add_worker_joins_it_directly_once_the_head_is_up(self) -> None:
        self.given(_cfg(HEAD))
        cli.add_worker("dgx", "10.0.0.2", None)

        self.assertEqual(self.sequence(), [("setup", "worker:direct", "10.0.0.2")])
        self.assertEqual(self.calls[0][3]["head_ip"], f"<{util.SECRET_CONTROL_PLANE_IP}>")
        self.assertEqual(self.config()["workers"], {"dgx": {"ip": "10.0.0.2", "done": ["worker:direct"]}})

    def test_add_worker_on_the_heads_ip_only_labels_the_head(self) -> None:
        self.given(_cfg(HEAD))
        cli.add_worker("p5", "10.0.0.1", None)

        self.assertEqual(self.sequence(), [("setup", "worker_on_head", "10.0.0.1")])
        self.assertEqual(self.config()["head"], HEAD)
        self.assertEqual(self.config()["workers"]["p5"]["done"], ["worker_on_head"])

    def test_add_aws_head_applies_the_stack_then_seeds_its_host(self) -> None:
        cli.add_aws_head(None)

        self.apply.assert_called_once_with()
        self.assertEqual(self.sequence(), [("setup", "head", "10.0.0.7")])
        self.assertEqual(
            self.config()["head"],
            {"ip": "10.0.0.7", "profile": "aws", "storage": "/storage", "done": ["head"]},
        )

    def test_add_aws_head_refuses_an_onprem_cluster_before_terraform(self) -> None:
        self.given(_cfg(HEAD))
        with self.assertRaises(SystemExit):
            cli.add_aws_head(None)

        self.apply.assert_not_called()


class TestDel(_ClusterTest):
    def test_del_head_refuses_while_workers_remain(self) -> None:
        self.given(_cfg(HEAD, p5=ON_HEAD, dgx=DGX))
        with self.assertRaises(SystemExit) as ctx:
            cli.del_head(None)

        message = str(ctx.exception)
        for line in ["./cg del worker p5", "./cg del worker dgx", "./cg del --all"]:
            self.assertIn(line, message)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.config(), _cfg(HEAD, p5=ON_HEAD, dgx=DGX))

    def test_del_worker_on_the_heads_ip_keeps_the_head(self) -> None:
        self.given(_cfg(HEAD, p5=ON_HEAD))
        cli.del_worker("p5", None)

        self.assertEqual(self.calls, [("teardown", "worker_on_head", "10.0.0.1", {"node_ip": "10.0.0.1"})])
        self.connect.assert_not_called()
        self.assertEqual(self.config(), _cfg(HEAD))

    def test_del_unknown_worker_names_the_known_ones(self) -> None:
        self.given(_cfg(HEAD, dgx=DGX))
        with self.assertRaises(SystemExit) as ctx:
            cli.del_worker("spark", None)

        self.assertIn("workers: dgx", str(ctx.exception))

    def test_del_all_removes_the_workers_then_the_head(self) -> None:
        self.given(_cfg(HEAD, p5=ON_HEAD, dgx=DGX))
        cli.del_all()

        self.assertEqual(self.sequence(), [
            ("teardown", "worker_on_head", "10.0.0.1"),
            ("teardown", "worker:direct", "10.0.0.2"),
            ("teardown", "head", "10.0.0.1"),
        ])
        self.assertEqual(self.config(), {"workers": {}})
        self.destroy.assert_not_called()

    def test_del_aws_head_destroys_the_stack_after_the_teardown(self) -> None:
        self.given(_cfg({**HEAD, "profile": "aws"}))
        self.destroy.side_effect = lambda: self.calls.append(("destroy", "aws", None, {}))
        cli.del_head(None)

        self.assertEqual(self.sequence(), [("teardown", "head", "10.0.0.1"), ("destroy", "aws", None)])
        self.assertEqual(self.config(), {"workers": {}})


class TestRestart(_ClusterTest):
    def test_tears_every_role_down_then_adds_it_back(self) -> None:
        self.given(_cfg(HEAD, p5=ON_HEAD, dgx=DGX))
        cli.restart()

        self.assertEqual(self.sequence(), [
            ("teardown", "worker_on_head", "10.0.0.1"),
            ("teardown", "worker:direct", "10.0.0.2"),
            ("teardown", "head", "10.0.0.1"),
            ("setup", "head", "10.0.0.1"),
            ("setup", "worker_on_head", "10.0.0.1"),
            ("setup", "worker:direct", "10.0.0.2"),
        ])
        self.assertEqual(self.config(), {
            "workers": {
                "p5": {"ip": "10.0.0.1", "done": ["worker_on_head"]},
                "dgx": {"ip": "10.0.0.2", "done": ["worker:direct"]},
            },
            "head": {**HEAD, "done": ["head"]},
        })
        self.apply.assert_not_called()
        self.destroy.assert_not_called()


class TestRecordStep(_ClusterTest):
    def test_setup_adds_a_step_once_and_teardown_removes_it(self) -> None:
        self.given(_cfg(HEAD))
        cli.record_step(None, "K3sServer", "setup")
        cli.record_step(None, "K3sServer", "setup")
        self.assertEqual(self.config()["head"]["done"], ["ArgoReady", "K3sServer"])

        cli.record_step(None, "ArgoReady", "teardown")
        cli.record_step(None, "Never", "teardown")
        self.assertEqual(self.config()["head"]["done"], ["K3sServer"])

    def test_a_worker_records_into_its_own_entry(self) -> None:
        self.given(_cfg(HEAD, p5=ON_HEAD))
        cli.record_step("p5", "ComputeLabels", "teardown")

        self.assertEqual(self.config()["workers"]["p5"]["done"], [])
        self.assertEqual(self.config()["head"], HEAD)


if __name__ == "__main__":
    unittest.main()
