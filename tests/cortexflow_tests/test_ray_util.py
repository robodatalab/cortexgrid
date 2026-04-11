from __future__ import annotations

import base64
import os
import pickle
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cortexflow.ray_util import remote, result, status, JobInfo
from cortexflow.config import CortexConfig, set_config
from ray.job_submission import JobStatus


class TestRemoteDecorator(unittest.TestCase):
    def setUp(self) -> None:
        set_config(
            CortexConfig(
                dgx_ip="100.1.2.3",
                mlflow_tracking_uri="http://100.1.2.3:5000",
                s3_access_key="key",
                s3_secret_key="secret",
            )
        )
        self.tmpdir = tempfile.mkdtemp()
        self.pyproject = Path(self.tmpdir) / "pyproject.toml"
        self.pyproject.write_text(
            textwrap.dedent("""\
            [project]
            name = "test-project"
            dependencies = ["numpy>=1.26"]
        """)
        )
        self.old_cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        set_config(None)  # type: ignore[arg-type]

    def test_runtime_env_includes_working_dir(self) -> None:
        wrapped = remote()(lambda: None)
        self.assertEqual(
            os.path.realpath(wrapped._runtime_env["working_dir"]),
            os.path.realpath(self.tmpdir),
        )

    def test_runtime_env_injects_service_env_vars(self) -> None:
        wrapped = remote()(lambda: None)
        env_vars = wrapped._runtime_env["env_vars"]
        self.assertEqual(env_vars["MLFLOW_TRACKING_URI"], "http://100.1.2.3:5000")
        self.assertEqual(env_vars["AWS_ACCESS_KEY_ID"], "key")
        self.assertEqual(env_vars["DGX_TAILSCALE_IP"], "100.1.2.3")

    def test_runtime_env_includes_excludes(self) -> None:
        wrapped = remote()(lambda: None)
        excludes = wrapped._runtime_env["excludes"]
        self.assertIn(".venv/", excludes)
        self.assertIn(".git/", excludes)
        self.assertIn("__pycache__/", excludes)


class TestStatus(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(dgx_ip="100.1.2.3"))
        self.mock_jsc = MagicMock()
        mock_status = MagicMock()
        mock_status.value = "RUNNING"
        self.mock_jsc.get_job_status.return_value = mock_status
        mock_info = MagicMock()
        mock_info.message = "In progress"
        self.mock_jsc.get_job_info.return_value = mock_info
        self.client_patcher = patch(
            "cortexflow.ray_util.JobSubmissionClient",
            return_value=self.mock_jsc,
        )
        self.client_patcher.start()

    def tearDown(self) -> None:
        self.client_patcher.stop()
        set_config(None)  # type: ignore[arg-type]

    def test_returns_job_info(self) -> None:
        info = status("raysubmit_abc123")
        self.assertIsInstance(info, JobInfo)
        self.assertEqual(info.job_id, "raysubmit_abc123")
        self.assertEqual(info.status, "RUNNING")
        self.assertEqual(info.message, "In progress")


class TestResult(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(dgx_ip="100.1.2.3"))
        self.mock_jsc = MagicMock()
        self.mock_jsc.get_job_status.return_value = JobStatus.SUCCEEDED
        self.client_patcher = patch(
            "cortexflow.ray_util.JobSubmissionClient",
            return_value=self.mock_jsc,
        )
        self.client_patcher.start()

    def tearDown(self) -> None:
        self.client_patcher.stop()
        set_config(None)  # type: ignore[arg-type]

    def test_returns_result_when_succeeded(self) -> None:
        self.mock_jsc.get_job_status.return_value = JobStatus.SUCCEEDED
        result_bytes = base64.b64encode(pickle.dumps({"acc": 0.95})).decode()
        self.mock_jsc.get_job_logs.return_value = (
            f"__CORTEXFLOW_RESULT__:{result_bytes}"
        )

        r = result("raysubmit_abc123")
        self.assertEqual(r, {"acc": 0.95})

    def test_raises_when_still_running(self) -> None:
        self.mock_jsc.get_job_status.return_value = JobStatus.RUNNING

        with self.assertRaises(RuntimeError) as ctx:
            result("raysubmit_abc123")
        self.assertIn("still RUNNING", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
