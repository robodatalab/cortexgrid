from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import cortexflow
import cortexflow.config
from cortexflow.config import CortexConfig, set_config
from cortexflow._ray_job_driver import main as ray_job_driver_main

class FakeJobSubmissionClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.pending: list[str] = []

    def submit_job(
        self, entrypoint: str, runtime_env: dict[str, Any], **kwargs: Any
    ) -> str:
        self.pending.append(runtime_env["working_dir"])
        return f"raysubmit_{len(self.pending)}"

    def run_all(self) -> None:
        for workdir in self.pending:
            ray_job_driver_main(str(Path(workdir) / "payload.pkl"))
        self.pending.clear()


class TestRemote(unittest.TestCase):
    def setUp(self) -> None:
        set_config(None)
        self.fake_jsc = FakeJobSubmissionClient()
        patchers = [
            patch("boto3.client"),
            patch("cortexflow.mlflow_util.MlflowClient"),
            patch(
                "cortexflow.ray_util.subprocess.run",
                return_value=MagicMock(stdout=""),
            ),
            patch(
                "cortexflow.ray_util.JobSubmissionClient",
                return_value=self.fake_jsc,
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(set_config, None)

    def test_submitted_job_executes(self) -> None:
        set_config(CortexConfig(ray_address="http://test:8265"))
        marker = str(Path(tempfile.mkdtemp()) / "marker")

        def write_marker() -> None:
            Path(marker).write_text("ran")

        cortexflow.remote(write_marker)
        self.assertFalse(Path(marker).exists())

        self.fake_jsc.run_all()

        self.assertTrue(Path(marker).exists())
        self.assertEqual(Path(marker).read_text(), "ran")

    def test_submitted_job_uses_config_active_at_submit_time(self) -> None:
        set_config(
            CortexConfig(experiment_name="exp-a", ray_address="http://test:8265")
        )
        out = str(Path(tempfile.mkdtemp()) / "exp")

        def capture_experiment() -> None:
            cfg = cortexflow.config.get_config()
            Path(out).write_text(cfg.experiment_name if cfg else "")

        cortexflow.remote(capture_experiment)

        set_config(
            CortexConfig(experiment_name="exp-b", ray_address="http://test:8265")
        )

        self.fake_jsc.run_all()

        self.assertEqual(Path(out).read_text(), "exp-a")

    def test_default_log_level_inside_job_is_info(self) -> None:
        set_config(CortexConfig(ray_address="http://test:8265"))

        with patch("logging.basicConfig") as mock_basic:
            cortexflow.remote(lambda: None)
            self.fake_jsc.run_all()

        mock_basic.assert_called()
        self.assertEqual(mock_basic.call_args.kwargs.get("level"), logging.INFO)


if __name__ == "__main__":
    unittest.main()
