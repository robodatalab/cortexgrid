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

    @patch("cortexflow.ray_util.ray")
    def test_decorator_passes_gpu_and_retries(self, mock_ray: MagicMock) -> None:
        mock_remote_fn = MagicMock()
        mock_ray.remote.return_value = mock_remote_fn

        from cortexflow.ray_util import remote
        decorator = remote(num_gpus=2, max_retries=5)

        def my_func() -> None:
            pass

        decorator(my_func)

        call_kwargs = mock_ray.remote.call_args[1]
        self.assertEqual(call_kwargs["num_gpus"], 2)
        self.assertEqual(call_kwargs["max_retries"], 5)

    @patch("cortexflow.ray_util.ray")
    def test_runtime_env_includes_pip_deps(self, mock_ray: MagicMock) -> None:
        mock_ray.remote.return_value = MagicMock()

        from cortexflow.ray_util import remote
        decorator = remote(num_gpus=1)
        decorator(lambda: None)

        call_kwargs = mock_ray.remote.call_args[1]
        runtime_env = call_kwargs["runtime_env"]
        self.assertIn("numpy>=1.26", runtime_env["pip"])

    @patch("cortexflow.ray_util.ray")
    def test_runtime_env_includes_working_dir(self, mock_ray: MagicMock) -> None:
        mock_ray.remote.return_value = MagicMock()

        from cortexflow.ray_util import remote
        decorator = remote()
        decorator(lambda: None)

        call_kwargs = mock_ray.remote.call_args[1]
        runtime_env = call_kwargs["runtime_env"]
        self.assertEqual(
            os.path.realpath(runtime_env["working_dir"]),
            os.path.realpath(self.tmpdir),
        )

    @patch("cortexflow.ray_util.ray")
    def test_runtime_env_injects_service_env_vars(self, mock_ray: MagicMock) -> None:
        mock_ray.remote.return_value = MagicMock()

        from cortexflow.ray_util import remote
        decorator = remote()
        decorator(lambda: None)

        call_kwargs = mock_ray.remote.call_args[1]
        env_vars = call_kwargs["runtime_env"]["env_vars"]
        self.assertEqual(env_vars["MLFLOW_TRACKING_URI"], "http://100.1.2.3:5000")
        self.assertEqual(env_vars["AWS_ACCESS_KEY_ID"], "key")
        self.assertEqual(env_vars["DGX_TAILSCALE_IP"], "100.1.2.3")

    @patch("cortexflow.ray_util.ray")
    def test_runtime_env_includes_excludes(self, mock_ray: MagicMock) -> None:
        mock_ray.remote.return_value = MagicMock()

        from cortexflow.ray_util import remote
        decorator = remote()
        decorator(lambda: None)

        call_kwargs = mock_ray.remote.call_args[1]
        excludes = call_kwargs["runtime_env"]["excludes"]
        self.assertIn(".venv/", excludes)
        self.assertIn(".git/", excludes)
        self.assertIn("__pycache__/", excludes)


class TestGet(unittest.TestCase):
    @patch("cortexflow.ray_util.ray")
    def test_delegates_to_ray_get(self, mock_ray: MagicMock) -> None:
        mock_ray.get.return_value = [1, 2, 3]

        from cortexflow.ray_util import get
        result = get(["fake_future"])

        mock_ray.get.assert_called_once_with(["fake_future"])
        self.assertEqual(result, [1, 2, 3])


class TestGetRayClient(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(ray_address="http://100.1.2.3:8265"))
        self.mock_jsc = MagicMock()
        self.modules_patcher = patch.dict(sys.modules, {
            "ray.job_submission": MagicMock(JobSubmissionClient=self.mock_jsc),
        })
        self.modules_patcher.start()

    def tearDown(self) -> None:
        self.modules_patcher.stop()
        set_config(None)  # type: ignore[arg-type]

    def test_returns_client_with_address(self) -> None:
        from cortexflow.ray_util import get_ray_client
        get_ray_client()
        self.mock_jsc.assert_called_once_with("http://100.1.2.3:8265")

    def test_raises_without_address(self) -> None:
        set_config(CortexConfig())
        from cortexflow.ray_util import get_ray_client
        with self.assertRaises(RuntimeError):
            get_ray_client()


if __name__ == "__main__":
    unittest.main()
