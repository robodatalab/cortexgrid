from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cortexflow.config import CortexConfig, set_config


class TestRemoteDecorator(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(
            dgx_ip="100.1.2.3",
            mlflow_tracking_uri="http://100.1.2.3:5000",
            s3_access_key="key",
            s3_secret_key="secret",
        ))
        self.tmpdir = tempfile.mkdtemp()
        self.pyproject = Path(self.tmpdir) / "pyproject.toml"
        self.pyproject.write_text(textwrap.dedent("""\
            [project]
            name = "test-project"
            dependencies = ["numpy>=1.26"]
        """))
        self.old_cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        set_config(None)  # type: ignore[arg-type]

    def test_returns_remote_function(self) -> None:
        from cortexflow.ray_util import remote, _RemoteFunction

        def my_func() -> None:
            pass

        wrapped = remote(num_gpus=2, max_retries=5)(my_func)
        self.assertIsInstance(wrapped, _RemoteFunction)

    def test_runtime_env_includes_pip_deps(self) -> None:
        from cortexflow.ray_util import remote

        wrapped = remote(num_gpus=1)(lambda: None)
        self.assertIn("numpy>=1.26", wrapped._runtime_env["pip"])

    def test_runtime_env_includes_working_dir(self) -> None:
        from cortexflow.ray_util import remote

        wrapped = remote()(lambda: None)
        self.assertEqual(
            os.path.realpath(wrapped._runtime_env["working_dir"]),
            os.path.realpath(self.tmpdir),
        )

    def test_runtime_env_injects_service_env_vars(self) -> None:
        from cortexflow.ray_util import remote

        wrapped = remote()(lambda: None)
        env_vars = wrapped._runtime_env["env_vars"]
        self.assertEqual(env_vars["MLFLOW_TRACKING_URI"], "http://100.1.2.3:5000")
        self.assertEqual(env_vars["AWS_ACCESS_KEY_ID"], "key")
        self.assertEqual(env_vars["DGX_TAILSCALE_IP"], "100.1.2.3")

    def test_runtime_env_includes_excludes(self) -> None:
        from cortexflow.ray_util import remote

        wrapped = remote()(lambda: None)
        excludes = wrapped._runtime_env["excludes"]
        self.assertIn(".venv/", excludes)
        self.assertIn(".git/", excludes)
        self.assertIn("__pycache__/", excludes)

    def test_bare_decorator_syntax(self) -> None:
        from cortexflow.ray_util import remote, _RemoteFunction

        @remote
        def my_func() -> None:
            pass

        self.assertIsInstance(my_func, _RemoteFunction)


class TestRemoteFunctionSubmit(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(dgx_ip="100.1.2.3"))
        self.mock_jsc = MagicMock()
        self.mock_jsc.submit_job.return_value = "raysubmit_abc123"
        self.modules_patcher = patch.dict(sys.modules, {
            "ray.job_submission": MagicMock(
                JobSubmissionClient=MagicMock(return_value=self.mock_jsc),
            ),
        })
        self.modules_patcher.start()

    def tearDown(self) -> None:
        self.modules_patcher.stop()
        set_config(None)  # type: ignore[arg-type]

    def test_remote_calls_submit_job(self) -> None:
        from cortexflow.ray_util import _RemoteFunction

        fn = _RemoteFunction(
            fn=_dummy_fn,
            num_gpus=1,
            max_retries=2,
            runtime_env={"working_dir": "."},
        )
        future = fn.remote(42)
        self.assertEqual(future.job_id, "raysubmit_abc123")
        self.mock_jsc.submit_job.assert_called_once()


def _dummy_fn(x: int) -> int:
    return x


class TestGet(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_status = MagicMock()
        self.mock_status.value = "SUCCEEDED"

    def test_extracts_result_from_logs(self) -> None:
        import base64
        import pickle
        from cortexflow.ray_util import _extract_result

        result_bytes = base64.b64encode(pickle.dumps(42)).decode()
        logs = f"some output\n__CORTEXFLOW_RESULT__:{result_bytes}\nmore output"
        self.assertEqual(_extract_result(logs), 42)

    def test_returns_none_when_no_marker(self) -> None:
        from cortexflow.ray_util import _extract_result
        self.assertIsNone(_extract_result("just some logs"))


class TestGetRayClient(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(dgx_ip="100.1.2.3"))
        self.modules_patcher = patch.dict(sys.modules, {
            "ray.job_submission": MagicMock(JobSubmissionClient=MagicMock()),
        })
        self.modules_patcher.start()

    def tearDown(self) -> None:
        self.modules_patcher.stop()
        set_config(None)  # type: ignore[arg-type]

    def test_returns_client(self) -> None:
        from cortexflow.ray_util import get_ray_client
        get_ray_client()

    def test_raises_without_dgx_ip(self) -> None:
        set_config(CortexConfig())
        from cortexflow.ray_util import get_ray_client
        with self.assertRaises(RuntimeError):
            get_ray_client()


if __name__ == "__main__":
    unittest.main()
