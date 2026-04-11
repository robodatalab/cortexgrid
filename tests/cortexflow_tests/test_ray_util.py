from __future__ import annotations

import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import cortexflow
from cortexflow.experiment import Experiment, clear_instance, set_instance
from cortexflow._ray_job_driver import main as ray_job_driver_main


def _make_experiment(experiment_name: str = "exp", run_id: str = "run") -> Experiment:
    return Experiment(
        experiment_name=experiment_name,
        run_id=run_id,
        ray_address="http://test:8265",
        dgx_ip="",
        mlflow_tracking_uri="",
        mlflow_s3_endpoint_url="",
        s3_endpoint_url="",
        s3_access_key="",
        s3_secret_key="",
        s3_default_bucket="",
        github_token="",
    )


class FakeJobSubmissionClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.pending: list[str] = []
        self.runtime_envs: list[dict[str, Any]] = []
        self.statuses: dict[str, str] = {}
        self.logs_by_id: dict[str, str] = {}

    def submit_job(
        self, entrypoint: str, runtime_env: dict[str, Any], **kwargs: Any
    ) -> str:
        self.pending.append(runtime_env["working_dir"])
        self.runtime_envs.append(runtime_env)
        return f"raysubmit_{len(self.pending)}"

    def get_job_status(self, job_id: str) -> SimpleNamespace:
        return SimpleNamespace(value=self.statuses.get(job_id, "PENDING"))

    def get_job_logs(self, job_id: str) -> str:
        return self.logs_by_id.get(job_id, "")

    def run_all(self) -> None:
        for workdir in self.pending:
            ray_job_driver_main(str(Path(workdir) / "payload.pkl"))
        self.pending.clear()


class TestRemote(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()

        self.project_root = Path(tempfile.mkdtemp(prefix="cortexflow-test-"))
        (self.project_root / "pyproject.toml").write_text("[project]\nname='test'\n")
        self.original_cwd = os.getcwd()
        os.chdir(self.project_root)
        self.addCleanup(os.chdir, self.original_cwd)
        self.addCleanup(shutil.rmtree, self.project_root, True)

        self.fake_jsc = FakeJobSubmissionClient()
        self.fake_mlflow = MagicMock()
        patchers = [
            patch("boto3.client"),
            patch("cortexflow.experiment.MlflowClient"),
            patch(
                "cortexflow.ray_util.MlflowClient",
                return_value=self.fake_mlflow,
            ),
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

    def tearDown(self) -> None:
        clear_instance()
        return super().tearDown()

    def test_submitted_job_executes(self) -> None:
        set_instance(_make_experiment())
        marker = str(Path(tempfile.mkdtemp()) / "marker")

        def write_marker() -> None:
            Path(marker).write_text("ran")

        cortexflow.remote(write_marker)
        self.assertFalse(Path(marker).exists())

        self.fake_jsc.run_all()

        self.assertTrue(Path(marker).exists())
        self.assertEqual(Path(marker).read_text(), "ran")

    def test_submitted_job_uses_experiment_active_at_submit_time(self) -> None:
        set_instance(_make_experiment(experiment_name="exp-a"))
        out = str(Path(tempfile.mkdtemp()) / "exp")

        def capture_experiment() -> None:
            exp = Experiment.get_instance()
            Path(out).write_text(exp.experiment_name)

        cortexflow.remote(capture_experiment)

        set_instance(_make_experiment(experiment_name="exp-b"))

        self.fake_jsc.run_all()

        self.assertEqual(Path(out).read_text(), "exp-a")

    def test_default_log_level_inside_job_is_info(self) -> None:
        set_instance(_make_experiment())

        with patch("logging.basicConfig") as mock_basic:
            cortexflow.remote(lambda: None)
            self.fake_jsc.run_all()

        mock_basic.assert_called()
        self.assertEqual(mock_basic.call_args.kwargs.get("level"), logging.INFO)

    def test_remote_registers_ray_job_id_with_mlflow(self) -> None:
        set_instance(_make_experiment(run_id="run-xyz"))

        job = cortexflow.remote(lambda: None)

        self.fake_mlflow.log_artifact.assert_called_once()
        args, kwargs = self.fake_mlflow.log_artifact.call_args
        self.assertEqual(args[0], "run-xyz")
        self.assertEqual(kwargs["artifact_path"], "ray-job")
        self.assertEqual(Path(args[1]).name, job.job_id)

    def test_get_ray_status_returns_status_string(self) -> None:
        exp = _make_experiment()
        set_instance(exp)
        self.fake_jsc.statuses["job-1"] = "RUNNING"

        self.assertEqual(cortexflow.get_ray_status(exp, "job-1"), "RUNNING")

    def test_get_ray_logs_returns_log_text(self) -> None:
        exp = _make_experiment()
        set_instance(exp)
        self.fake_jsc.logs_by_id["job-1"] = "hello from the cluster"

        self.assertEqual(
            cortexflow.get_ray_logs(exp, "job-1"), "hello from the cluster"
        )

    def test_remote_uses_fresh_tempdir_as_working_dir(self) -> None:
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None)

        workdir = Path(self.fake_jsc.runtime_envs[0]["working_dir"])
        self.assertNotEqual(workdir, self.project_root)
        self.assertTrue(workdir.exists())
        self.assertTrue(workdir.name.startswith("cortexflow-"))

    def test_remote_copies_project_files_into_workdir(self) -> None:
        (self.project_root / "train.py").write_text("RESULT = 'loaded'\n")
        (self.project_root / "pkg").mkdir()
        (self.project_root / "pkg" / "__init__.py").write_text("")
        (self.project_root / "pkg" / "model.py").write_text("MODEL = 'cnn'\n")
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None)

        workdir = Path(self.fake_jsc.runtime_envs[0]["working_dir"])
        self.assertTrue((workdir / "pyproject.toml").exists())
        self.assertTrue((workdir / "train.py").exists())
        self.assertTrue((workdir / "pkg" / "model.py").exists())
        self.assertEqual((workdir / "train.py").read_text(), "RESULT = 'loaded'\n")
        self.assertTrue((workdir / "payload.pkl").exists())
        self.assertTrue((workdir / "requirements.txt").exists())

    def test_remote_skips_default_excludes_when_copying(self) -> None:
        (self.project_root / ".venv").mkdir()
        (self.project_root / ".venv" / "should_not_ship.txt").write_text("nope")
        (self.project_root / ".git").mkdir()
        (self.project_root / ".git" / "HEAD").write_text("ref: refs/heads/main")
        (self.project_root / "__pycache__").mkdir()
        (self.project_root / "__pycache__" / "x.pyc").write_text("")
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None)

        workdir = Path(self.fake_jsc.runtime_envs[0]["working_dir"])
        self.assertFalse((workdir / ".venv").exists())
        self.assertFalse((workdir / ".git").exists())
        self.assertFalse((workdir / "__pycache__").exists())

    def test_remote_finds_pyproject_in_parent_dir(self) -> None:
        subdir = self.project_root / "src" / "nested"
        subdir.mkdir(parents=True)
        os.chdir(subdir)
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None)

        workdir = Path(self.fake_jsc.runtime_envs[0]["working_dir"])
        self.assertTrue((workdir / "pyproject.toml").exists())
        self.assertTrue((workdir / "src" / "nested").exists())


if __name__ == "__main__":
    unittest.main()
