from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from cortexgrid import secrets


@patch.dict(os.environ, {"CORTEXGRID_HEAD_URL": "http://robolab-head:7700/"})
class TestSecretsClient(unittest.TestCase):
    """cortexgrid.secrets calls the head secrets server at $CORTEXGRID_HEAD_URL.
    The server side is covered end to end in tests/unit/k8s/seed/scripts."""

    @patch("cortexgrid.secrets.requests.get")
    def test_get_secret_quotes_id_and_returns_value(self, mock_get: MagicMock) -> None:
        mock_get.return_value.json.return_value = {"value": "v"}
        self.assertEqual(secrets.get_secret("a/b"), "v")
        mock_get.assert_called_once_with(
            "http://robolab-head:7700/secrets/a%2Fb", timeout=secrets._TIMEOUT_S
        )

    @patch("cortexgrid.secrets.requests.get")
    def test_list_secrets(self, mock_get: MagicMock) -> None:
        mock_get.return_value.json.return_value = ["A", "B"]
        self.assertEqual(secrets.list_secrets(), ["A", "B"])
        mock_get.assert_called_once_with(
            "http://robolab-head:7700/secrets", timeout=secrets._TIMEOUT_S
        )

    @patch("cortexgrid.secrets.requests.put")
    def test_set_secret_puts_value(self, mock_put: MagicMock) -> None:
        secrets.set_secret("KEY", "v")
        mock_put.assert_called_once_with(
            "http://robolab-head:7700/secrets/KEY",
            json={"value": "v"},
            timeout=secrets._TIMEOUT_S,
        )
        mock_put.return_value.raise_for_status.assert_called_once()

    @patch("cortexgrid.secrets.requests.delete")
    def test_delete_secret(self, mock_delete: MagicMock) -> None:
        secrets.delete_secret("KEY")
        mock_delete.assert_called_once_with(
            "http://robolab-head:7700/secrets/KEY", timeout=secrets._TIMEOUT_S
        )
        mock_delete.return_value.raise_for_status.assert_called_once()

    def test_missing_head_url_raises(self) -> None:
        with patch.dict(os.environ, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                secrets.get_secret("KEY")
        self.assertIn("CORTEXGRID_HEAD_URL", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
