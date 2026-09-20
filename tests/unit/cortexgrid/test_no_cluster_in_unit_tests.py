from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from cortexgrid.infra import get_mlflow_tracking_uri, get_ray_job_server_uri
from cortexgrid.secrets import get_secret


@patch.dict(os.environ, {"CORTEXGRID_HEAD_URL": "http://head.invalid:7700"})
class TestUnitTestsCannotReachTheCluster(unittest.TestCase):
    """The guard in tests/unit/__init__.py, pinned.

    A head URL is exported in a developer's shell, so these calls would
    otherwise be answered by the real cluster.
    """

    def test_asking_for_a_secret_fails_instead_of_answering(self) -> None:
        with self.assertRaises(AssertionError):
            get_secret("MLFLOW_TRACKING_URI")

    def test_reading_the_mlflow_uri_fails_instead_of_answering(self) -> None:
        with self.assertRaises(AssertionError):
            get_mlflow_tracking_uri()

    def test_reading_the_ray_uri_fails_instead_of_answering(self) -> None:
        with self.assertRaises(AssertionError):
            get_ray_job_server_uri()

    def test_the_message_says_what_to_do_about_it(self) -> None:
        with self.assertRaises(AssertionError) as caught:
            get_mlflow_tracking_uri()

        self.assertIn("Patch the boundary", str(caught.exception))
        self.assertIn("tests/fakes.py", str(caught.exception))

    def test_a_test_that_patches_the_boundary_is_unaffected(self) -> None:
        with patch("cortexgrid.infra.get_secret", return_value="sqlite:///x"):
            self.assertEqual(get_mlflow_tracking_uri(), "sqlite:///x")

    def test_a_secrets_server_on_this_machine_is_reached_normally(self) -> None:
        """A test that stands up its own server is hermetic, so it is allowed."""
        with patch.dict(
            os.environ, {"CORTEXGRID_HEAD_URL": "http://127.0.0.1:7700"}
        ):
            with patch("tests.unit.requests.get") as sent:
                sent.return_value.json.return_value = {"value": "v"}

                self.assertEqual(get_secret("a"), "v")

        sent.assert_called_once()

    def test_the_secrets_client_itself_can_still_be_tested(self) -> None:
        """Patching the transport works exactly as it does on real requests."""
        with patch("cortexgrid.secrets.requests.get") as request:
            request.return_value.json.return_value = {"value": "v"}

            self.assertEqual(get_secret("a"), "v")

        with self.assertRaises(AssertionError):
            get_secret("a")


if __name__ == "__main__":
    unittest.main()
