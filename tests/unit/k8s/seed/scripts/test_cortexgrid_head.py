from __future__ import annotations

import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import requests
from parameterized import parameterized  # type: ignore

from cortexgrid import secrets
from k8s.seed.scripts import cortexgrid_head


class TestEnvFormat(unittest.TestCase):
    """format_env output parses back to the same values, and hand-written .env syntax parses."""

    @parameterized.expand(
        [
            ("plain", "abc123"),
            ("empty", ""),
            ("spaces", "  padded value  "),
            ("equals_and_hash", "a=b # not a comment"),
            ("quotes", "it's \"quoted\""),
            ("backslashes", "C:\\path\\n-not-a-newline"),
            ("multiline_pem", "-----BEGIN KEY-----\nabc=\ndef==\n-----END KEY-----\n"),
        ]
    )
    def test_format_then_parse_roundtrips(self, _name: str, value: str) -> None:
        values = {"FIRST": "1", "KEY": value, "LAST": "2"}
        self.assertEqual(
            cortexgrid_head.parse_env(cortexgrid_head.format_env(values)), values
        )

    def test_parses_hand_written_syntax(self) -> None:
        text = (
            "# comment\n"
            "\n"
            "export EXPORTED=1\n"
            "BARE=value # trailing comment\n"
            "SINGLE='kept # as is'\n"
            'MULTI="-----BEGIN KEY-----\n'
            "abc==\n"
            '-----END KEY-----"\n'
            "it-hyphenated=x\n"
        )
        self.assertEqual(
            cortexgrid_head.parse_env(text),
            {
                "EXPORTED": "1",
                "BARE": "value",
                "SINGLE": "kept # as is",
                "MULTI": "-----BEGIN KEY-----\nabc==\n-----END KEY-----",
                "it-hyphenated": "x",
            },
        )


class TestSecretsServer(unittest.TestCase):
    """The head server, driven through the cortexgrid.secrets client."""

    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.env_path = Path(tmpdir.name) / "cortexgrid" / ".env"
        server = cortexgrid_head.SecretsServer(
            ("127.0.0.1", 0), cortexgrid_head.EnvFileStore(self.env_path)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}"
        env_patch = mock.patch.dict(os.environ, {"CORTEXGRID_HEAD_URL": self.url + "/"})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_set_get_list_delete_roundtrip(self) -> None:
        pem = "-----BEGIN KEY-----\nabc\n-----END KEY-----"
        secrets.set_secret("GH_APP_PRIVATE_KEY", pem)
        secrets.set_secret("it-1234-roundtrip", "hello")
        self.assertEqual(secrets.get_secret("GH_APP_PRIVATE_KEY"), pem)
        self.assertEqual(
            secrets.list_secrets(), ["GH_APP_PRIVATE_KEY", "it-1234-roundtrip"]
        )
        secrets.delete_secret("it-1234-roundtrip")
        self.assertEqual(secrets.list_secrets(), ["GH_APP_PRIVATE_KEY"])

    def test_set_overwrites(self) -> None:
        secrets.set_secret("KEY", "old")
        secrets.set_secret("KEY", "new")
        self.assertEqual(secrets.get_secret("KEY"), "new")

    def test_delete_missing_is_success(self) -> None:
        secrets.delete_secret("NEVER_SET")

    def test_get_missing_raises_404(self) -> None:
        with self.assertRaises(requests.HTTPError) as ctx:
            secrets.get_secret("NEVER_SET")
        self.assertEqual(ctx.exception.response.status_code, 404)

    def test_store_file_is_private_and_survives_restart(self) -> None:
        secrets.set_secret("KEY", "value")
        self.assertEqual(stat.S_IMODE(self.env_path.stat().st_mode), 0o600)
        self.assertEqual(
            cortexgrid_head.EnvFileStore(self.env_path).get("KEY"), "value"
        )

    @parameterized.expand(
        [
            ("invalid_id", "put", "/secrets/bad%20id", {"value": "x"}, 400),
            ("body_without_value", "put", "/secrets/KEY", {"nope": "x"}, 400),
            ("non_string_value", "put", "/secrets/KEY", {"value": 1}, 400),
            ("unknown_route", "get", "/other", None, 404),
        ]
    )
    def test_rejects_bad_requests(
        self, _name: str, method: str, path: str, body: dict | None, status: int
    ) -> None:
        response = requests.request(method, self.url + path, json=body, timeout=5)
        self.assertEqual(response.status_code, status)


if __name__ == "__main__":
    unittest.main()
