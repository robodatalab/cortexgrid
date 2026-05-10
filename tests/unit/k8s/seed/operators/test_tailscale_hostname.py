from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests

from k8s.seed.operators.tailscale_hostname import TailscaleHostname


def _status(self_name: str, peers_by_name: dict[str, dict] | None = None) -> str:
    """Build a tailscale status --json payload with Self.HostName + optional peers."""
    peers = {
        f"key-{i}": {"HostName": name, **info}
        for i, (name, info) in enumerate((peers_by_name or {}).items())
    }
    return json.dumps({"Self": {"HostName": self_name}, "Peer": peers})


class _FakeConnection:
    """Records every sudo() call and returns scripted stdout for tailscale status."""

    def __init__(self, status_payload: str):
        self.host = "test-host"
        self._status_payload = status_payload
        self.sudo_calls: list[str] = []

    def sudo(self, cmd: str, hide: bool = False):
        self.sudo_calls.append(cmd)
        if cmd == "tailscale status --json":
            return SimpleNamespace(stdout=self._status_payload)
        return SimpleNamespace(stdout="")


class TestTailscaleHostnameRename(unittest.TestCase):
    """Renaming this machine to robolab-head only happens when the name is free."""

    def test_renames_when_no_peer_holds_robolab_head(self) -> None:
        """Stock hostname + nobody else owns robolab-head -> rename runs."""
        c = _FakeConnection(_status(self_name="spark-40f4", peers_by_name={
            "piotrs-macbook-pro": {"ID": "1", "OS": "macOS", "Online": True},
        }))
        TailscaleHostname().setup({"connection": c})
        self.assertIn("tailscale set --hostname=robolab-head", c.sudo_calls)

    def test_no_rename_when_self_already_robolab_head(self) -> None:
        """Idempotent: re-running setup on a head already named robolab-head is a no-op."""
        c = _FakeConnection(_status(self_name="robolab-head"))
        TailscaleHostname().setup({"connection": c})
        self.assertNotIn(
            "tailscale set --hostname=robolab-head",
            c.sudo_calls,
            "Must not re-issue the rename when the machine already owns the name.",
        )

    def test_no_rename_when_another_peer_holds_robolab_head(self) -> None:
        """An online stale robolab-head device blocks the rename to avoid -1 suffix."""
        c = _FakeConnection(_status(self_name="spark-40f4", peers_by_name={
            "robolab-head": {"ID": "stale-id", "OS": "linux", "Online": True},
        }))
        TailscaleHostname().setup({"connection": c})
        self.assertNotIn(
            "tailscale set --hostname=robolab-head",
            c.sudo_calls,
            "Must skip rename when a different device already holds robolab-head.",
        )

    def test_no_rename_when_offline_peer_holds_robolab_head(self) -> None:
        """Offline orphan in the admin panel still blocks the rename -- it would auto-suffix."""
        c = _FakeConnection(_status(self_name="spark-40f4", peers_by_name={
            "robolab-head": {"ID": "stale-id", "OS": "linux", "Online": False},
        }))
        TailscaleHostname().setup({"connection": c})
        self.assertNotIn(
            "tailscale set --hostname=robolab-head",
            c.sudo_calls,
            "Offline stale entries also cause auto-suffix; must skip rename.",
        )


class TestTailscaleHostnameLeavesOtherTagsAlone(unittest.TestCase):
    """ray and tailscale-operator tagged devices are out of scope for setup."""

    def test_rename_runs_even_when_ray_peer_exists(self) -> None:
        """A pre-existing ray tag must NOT block the robolab-head rename."""
        c = _FakeConnection(_status(self_name="spark-40f4", peers_by_name={
            "ray": {"ID": "ray-id", "OS": "linux", "Online": False},
        }))
        TailscaleHostname().setup({"connection": c})
        self.assertIn("tailscale set --hostname=robolab-head", c.sudo_calls)

    def test_rename_runs_even_when_tailscale_operator_peer_exists(self) -> None:
        """A pre-existing tailscale-operator tag must NOT block the rename."""
        c = _FakeConnection(_status(self_name="spark-40f4", peers_by_name={
            "tailscale-operator": {"ID": "op-id", "OS": "linux", "Online": False},
        }))
        TailscaleHostname().setup({"connection": c})
        self.assertIn("tailscale set --hostname=robolab-head", c.sudo_calls)

    def test_operator_never_issues_commands_for_ray_or_tailscale_operator(self) -> None:
        """Sanity: only `tailscale status --json` and the single rename ever get sudo'd."""
        c = _FakeConnection(_status(self_name="spark-40f4", peers_by_name={
            "ray": {"ID": "ray-id", "OS": "linux", "Online": False},
            "tailscale-operator": {"ID": "op-id", "OS": "linux", "Online": False},
        }))
        TailscaleHostname().setup({"connection": c})
        self.assertEqual(
            c.sudo_calls,
            ["tailscale status --json", "tailscale set --hostname=robolab-head"],
        )


def _patch_api(devices: list[dict], delete_recorder: list[str], delete_raises: set[str] | None = None):
    """Patch tailscale_hostname API helpers with in-memory fakes.

    Returns the patches as a context manager via contextlib.ExitStack — caller
    uses `with _patch_api(...) as ...:` style. The fakes record every device
    id that would have been deleted, and simulate delete failures for ids in
    delete_raises.
    """
    delete_raises = delete_raises or set()

    def fake_token(_client_id: str, _client_secret: str) -> str:
        return "fake-token"

    def fake_list(_token: str) -> list[dict]:
        return devices

    def fake_delete(_token: str, device_id: str) -> None:
        if device_id in delete_raises:
            raise requests.RequestException(f"simulated delete failure for {device_id}")
        delete_recorder.append(device_id)

    return mock.patch.multiple(
        "k8s.seed.operators.tailscale_hostname",
        _tailscale_api_token=fake_token,
        _tailscale_list_devices=fake_list,
        _tailscale_delete_device=fake_delete,
    )


def _temp_env_file(tmpdir: str, **vars: str) -> Path:
    """Write a .env file under tmpdir and return its path."""
    path = Path(tmpdir) / ".env"
    path.write_text("\n".join(f'{k}="{v}"' for k, v in vars.items()))
    return path


class TestTailscaleHostnameTeardown(unittest.TestCase):
    """Teardown deletes only tag:k8s devices and never touches robolab-head."""

    def test_deletes_every_k8s_tagged_device(self) -> None:
        """All devices carrying tag:k8s get deleted via API."""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = _temp_env_file(tmp, TS_OAUTH_CLIENT_ID="id", TS_OAUTH_SECRET="secret")
            deleted: list[str] = []
            with _patch_api(devices=[
                {"id": "d1", "name": "ray.tail-x.ts.net", "tags": ["tag:k8s"]},
                {"id": "d2", "name": "tailscale-operator.tail-x.ts.net", "tags": ["tag:k8s"]},
            ], delete_recorder=deleted):
                TailscaleHostname().teardown({"env_file": str(env_file)})
            self.assertEqual(sorted(deleted), ["d1", "d2"])

    def test_leaves_robolab_head_alone(self) -> None:
        """robolab-head is the head's only entry point; it must never be deleted."""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = _temp_env_file(tmp, TS_OAUTH_CLIENT_ID="id", TS_OAUTH_SECRET="secret")
            deleted: list[str] = []
            with _patch_api(devices=[
                {"id": "head-id", "name": "robolab-head.tail-x.ts.net", "tags": []},
                {"id": "ray-id", "name": "ray.tail-x.ts.net", "tags": ["tag:k8s"]},
            ], delete_recorder=deleted):
                TailscaleHostname().teardown({"env_file": str(env_file)})
            self.assertEqual(deleted, ["ray-id"])
            self.assertNotIn("head-id", deleted)

    def test_leaves_user_devices_alone(self) -> None:
        """Personal devices (no tag:k8s) are never deleted."""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = _temp_env_file(tmp, TS_OAUTH_CLIENT_ID="id", TS_OAUTH_SECRET="secret")
            deleted: list[str] = []
            with _patch_api(devices=[
                {"id": "laptop", "name": "piotrs-macbook-pro.tail-x.ts.net", "tags": []},
                {"id": "spark", "name": "spark-40f4.tail-x.ts.net", "tags": []},
            ], delete_recorder=deleted):
                TailscaleHostname().teardown({"env_file": str(env_file)})
            self.assertEqual(deleted, [])

    def test_no_op_when_nothing_to_clean(self) -> None:
        """No tag:k8s devices in the tailnet -> teardown is a quiet no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = _temp_env_file(tmp, TS_OAUTH_CLIENT_ID="id", TS_OAUTH_SECRET="secret")
            deleted: list[str] = []
            with _patch_api(devices=[
                {"id": "head-id", "name": "robolab-head.tail-x.ts.net", "tags": []},
            ], delete_recorder=deleted):
                TailscaleHostname().teardown({"env_file": str(env_file)})
            self.assertEqual(deleted, [])

    def test_partial_failure_does_not_abort_other_deletes(self) -> None:
        """Best-effort: one device's delete failing must not stop the others."""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = _temp_env_file(tmp, TS_OAUTH_CLIENT_ID="id", TS_OAUTH_SECRET="secret")
            deleted: list[str] = []
            with _patch_api(devices=[
                {"id": "d1", "name": "ray.tail-x.ts.net", "tags": ["tag:k8s"]},
                {"id": "d2", "name": "tailscale-operator.tail-x.ts.net", "tags": ["tag:k8s"]},
                {"id": "d3", "name": "ray-1.tail-x.ts.net", "tags": ["tag:k8s"]},
            ], delete_recorder=deleted, delete_raises={"d2"}):
                # Must not raise.
                TailscaleHostname().teardown({"env_file": str(env_file)})
            self.assertEqual(sorted(deleted), ["d1", "d3"])

    def test_missing_oauth_secrets_skip_cleanup_without_raising(self) -> None:
        """If .env lacks the OAuth creds, teardown logs and continues -- never aborts."""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = _temp_env_file(tmp)  # empty .env
            # Patch list/delete to raise if called, so we catch any accidental API call.
            with mock.patch(
                "k8s.seed.operators.tailscale_hostname._tailscale_list_devices",
                side_effect=AssertionError("must not call list when no creds"),
            ):
                TailscaleHostname().teardown({"env_file": str(env_file)})

    def test_api_unreachable_skips_cleanup_without_raising(self) -> None:
        """Transient API failure on token/list must not abort teardown."""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = _temp_env_file(tmp, TS_OAUTH_CLIENT_ID="id", TS_OAUTH_SECRET="secret")
            with mock.patch(
                "k8s.seed.operators.tailscale_hostname._tailscale_api_token",
                side_effect=requests.RequestException("network down"),
            ):
                # Must not raise.
                TailscaleHostname().teardown({"env_file": str(env_file)})


if __name__ == "__main__":
    unittest.main()
