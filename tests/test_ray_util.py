from __future__ import annotations

import base64
import os
import pickle
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ray.job_submission import JobStatus

from cortexflow.config import CortexConfig, set_config


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
        self.client_patcher = patch(
            "cortexflow.ray_util.JobSubmissionClient",
            return_value=self.mock_jsc,
        )
        self.client_patcher.start()

    def tearDown(self) -> None:
        self.client_patcher.stop()
        set_config(None)  # type: ignore[arg-type]

    def test_remote_returns_job_with_ids(self) -> None:
        from cortexflow.ray_util import _RemoteFunction, Job

        fn = _RemoteFunction(
            fn=_dummy_fn,
            num_gpus=1,
            max_retries=2,
            runtime_env={"working_dir": "."},
        )
        job = fn.remote(42)
        self.assertIsInstance(job, Job)
        self.assertEqual(job.job_id, "raysubmit_abc123")
        self.assertIsNotNone(job.cortexflow_job_id)
        self.mock_jsc.submit_job.assert_called_once()

    def test_submit_includes_cortexflow_job_id_in_metadata(self) -> None:
        from cortexflow.ray_util import _RemoteFunction

        fn = _RemoteFunction(
            fn=_dummy_fn,
            num_gpus=1,
            max_retries=0,
            runtime_env={"working_dir": "."},
        )
        job = fn.remote(42)
        call_kwargs = self.mock_jsc.submit_job.call_args
        self.assertIn("cortexflow_job_id", call_kwargs.kwargs["metadata"])
        self.assertEqual(
            call_kwargs.kwargs["metadata"]["cortexflow_job_id"],
            job.cortexflow_job_id,
        )

    def test_driver_includes_retry_and_job_id(self) -> None:
        from cortexflow.ray_util import _RemoteFunction

        fn = _RemoteFunction(
            fn=_dummy_fn,
            num_gpus=1,
            max_retries=3,
            runtime_env={"working_dir": "."},
        )
        fn.remote(42)
        entrypoint = self.mock_jsc.submit_job.call_args.kwargs["entrypoint"]
        self.assertIn("CORTEXFLOW_JOB_ID", entrypoint)
        self.assertIn("[cortexflow] Retry", entrypoint)


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

    def tearDown(self) -> None:
        set_config(None)  # type: ignore[arg-type]

    @patch("cortexflow.ray_util.JobSubmissionClient")
    def test_returns_client(self, _mock_cls: MagicMock) -> None:
        from cortexflow.ray_util import get_ray_client

        get_ray_client()

    def test_raises_without_dgx_ip(self) -> None:
        set_config(CortexConfig())
        from cortexflow.ray_util import get_ray_client

        with self.assertRaises(RuntimeError):
            get_ray_client()


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
        from cortexflow.ray_util import status, JobInfo

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

        from cortexflow.ray_util import result

        r = result("raysubmit_abc123")
        self.assertEqual(r, {"acc": 0.95})

    def test_raises_when_still_running(self) -> None:
        self.mock_jsc.get_job_status.return_value = JobStatus.RUNNING

        from cortexflow.ray_util import result

        with self.assertRaises(RuntimeError) as ctx:
            result("raysubmit_abc123")
        self.assertIn("still RUNNING", str(ctx.exception))


class TestLogs(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(dgx_ip="100.1.2.3"))
        self.mock_jsc = MagicMock()
        self.mock_jsc.get_job_logs.return_value = "epoch 1: loss=0.5"
        self.client_patcher = patch(
            "cortexflow.ray_util.JobSubmissionClient",
            return_value=self.mock_jsc,
        )
        self.client_patcher.start()

    def tearDown(self) -> None:
        self.client_patcher.stop()
        set_config(None)  # type: ignore[arg-type]

    def test_returns_log_string(self) -> None:
        from cortexflow.ray_util import logs

        self.assertIn("epoch 1", logs("raysubmit_abc123"))


class TestJobRepr(unittest.TestCase):
    def test_repr(self) -> None:
        from cortexflow.ray_util import Job

        job = Job(client=None, job_id="raysubmit_abc123")
        self.assertEqual(repr(job), "Job('raysubmit_abc123')")


if __name__ == "__main__":
    unittest.main()
