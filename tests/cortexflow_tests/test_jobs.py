from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cloudpickle  # type: ignore

import cortexflow
from cortexflow.experiment import Experiment, clear_instance, set_instance
from cortexflow.jobs import JobLifecycle, JobStatus, Payload


def _make_experiment(experiment_name: str = "exp", run_id: str = "run") -> Experiment:
    return Experiment(
        experiment_name=experiment_name,
        run_id=run_id,
    )


class FakeMLflow:
    """Fake MlflowClient backed by a real temp directory."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def log_artifact(self, run_id: str, local_path: str, artifact_path: str = "") -> None:
        dest = self.root / artifact_path
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest / Path(local_path).name)

    def log_artifacts(self, run_id: str, local_dir: str, artifact_path: str = "") -> None:
        dest = self.root / artifact_path
        shutil.copytree(local_dir, str(dest), dirs_exist_ok=True)

    def download_artifacts(self, run_id: str, path: str) -> str:
        return str(self.root / path)

    def list_artifacts(self, run_id: str, path: str = "") -> list:
        return []


class TestRemote(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()
        self.fake_mlflow = FakeMLflow()
        self.project_dir = Path(tempfile.mkdtemp())
        (self.project_dir / "pyproject.toml").write_text("[project]\nname='test'\n")
        (self.project_dir / "src").mkdir()
        (self.project_dir / "src" / "main.py").write_text("print('hello')")
        self.original_cwd = os.getcwd()
        os.chdir(self.project_dir)

        patchers = [
            patch("cortexflow.experiment.MlflowClient"),
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch("cortexflow.jobs.subprocess.run", return_value=MagicMock(stdout="numpy==1.26\ntorch==2.5\ncortexflow @ git+https://github.com/paksas/robolab-infra.git@abc123\n")),
            patch("cortexflow.jobs.get_secret", return_value="ghp_faketoken"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(os.chdir, self.original_cwd)
        self.addCleanup(shutil.rmtree, str(self.project_dir), True)

    def tearDown(self) -> None:
        clear_instance()

    def test_remote_returns_job_id_string(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        self.assertIsInstance(job_id, str)
        self.assertTrue(len(job_id) > 0)

    def test_remote_uploads_payload_and_lifecycle(self) -> None:
        set_instance(_make_experiment(run_id="run-xyz"))

        job_id = cortexflow.remote(lambda: None)

        self.assertTrue((self.fake_mlflow.root / "job" / job_id / "payload.pkl").exists())
        self.assertTrue((self.fake_mlflow.root / "job" / job_id / "lifecycle.json").exists())

    def test_remote_uploads_project_code(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        project_root = self.fake_mlflow.root / "job" / job_id / "project_code_root"
        self.assertTrue(project_root.is_dir())
        self.assertTrue((project_root / "pyproject.toml").exists())
        self.assertTrue((project_root / "src" / "main.py").exists())

    def test_remote_writes_requirements_txt(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        requirements = self.fake_mlflow.root / "job" / job_id / "project_code_root" / "requirements.txt"
        self.assertTrue(requirements.exists())
        self.assertIn("numpy==1.26", requirements.read_text())
        self.assertIn("torch==2.5", requirements.read_text())

    def test_remote_injects_github_token_into_git_urls(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        requirements = self.fake_mlflow.root / "job" / job_id / "project_code_root" / "requirements.txt"
        content = requirements.read_text()
        self.assertIn("git+https://x-access-token:ghp_faketoken@github.com/paksas/robolab-infra.git", content)
        self.assertNotIn("git+https://github.com/paksas/robolab-infra.git", content)

    def test_remote_excludes_venv_and_git(self) -> None:
        (self.project_dir / ".venv").mkdir()
        (self.project_dir / ".venv" / "lib.py").write_text("x")
        (self.project_dir / ".git").mkdir()
        (self.project_dir / ".git" / "config").write_text("x")
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        project_root = self.fake_mlflow.root / "job" / job_id / "project_code_root"
        self.assertFalse((project_root / ".venv").exists())
        self.assertFalse((project_root / ".git").exists())

    def test_initial_lifecycle_is_pending(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        raw = (self.fake_mlflow.root / "job" / job_id / "lifecycle.json").read_text()
        lifecycle = JobLifecycle.from_json(raw)
        self.assertEqual(lifecycle.status, JobStatus.PENDING)
        self.assertIsNone(lifecycle.error)
        self.assertFalse(lifecycle.retry)

    def test_lifecycle_includes_retry_flag(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None, retry=True)

        raw = (self.fake_mlflow.root / "job" / job_id / "lifecycle.json").read_text()
        lifecycle = JobLifecycle.from_json(raw)
        self.assertTrue(lifecycle.retry)

    def test_payload_round_trips_through_cloudpickle(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        raw = (self.fake_mlflow.root / "job" / job_id / "payload.pkl").read_bytes()
        payload: Payload = cloudpickle.loads(raw)
        self.assertIsNotNone(payload.fn)
        self.assertEqual(payload.experiment.experiment_name, "exp")

    def test_payload_includes_resource_requests(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None, num_gpus=2, num_cpus=4)

        raw = (self.fake_mlflow.root / "job" / job_id / "payload.pkl").read_bytes()
        payload: Payload = cloudpickle.loads(raw)
        self.assertEqual(payload.num_gpus, 2)
        self.assertEqual(payload.num_cpus, 4)


class TestPayloadSaveLoad(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()
        self.fake_mlflow = FakeMLflow()
        self.project_dir = Path(tempfile.mkdtemp())
        (self.project_dir / "pyproject.toml").write_text("[project]\nname='test'\n")
        (self.project_dir / "train.py").write_text("import torch")
        (self.project_dir / "data").mkdir()
        (self.project_dir / "data" / "config.yaml").write_text("lr: 0.001")

        patchers = [
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, str(self.project_dir), True)

    def tearDown(self) -> None:
        clear_instance()

    def _make_payload(self, exp: Experiment, job_id: str = "job-1") -> Payload:
        return Payload(
            experiment=exp,
            job_id=job_id,
            fn=lambda x: x * 2,
            args=(42,),
            kwargs={},
            project_code_root=str(self.project_dir),
        )

    def test_save_creates_payload_pkl(self) -> None:
        exp = _make_experiment()
        payload = self._make_payload(exp)

        payload.save_to_mlflow()

        self.assertTrue((self.fake_mlflow.root / "job" / "job-1" / "payload.pkl").exists())

    def test_save_creates_project_code_root_dir(self) -> None:
        exp = _make_experiment()
        payload = self._make_payload(exp)

        payload.save_to_mlflow()

        project = self.fake_mlflow.root / "job" / "job-1" / "project_code_root"
        self.assertTrue(project.is_dir())

    def test_save_copies_project_files(self) -> None:
        exp = _make_experiment()
        payload = self._make_payload(exp)

        payload.save_to_mlflow()

        project = self.fake_mlflow.root / "job" / "job-1" / "project_code_root"
        self.assertTrue((project / "pyproject.toml").exists())
        self.assertTrue((project / "train.py").exists())
        self.assertTrue((project / "data" / "config.yaml").exists())

    def test_save_preserves_file_contents(self) -> None:
        exp = _make_experiment()
        payload = self._make_payload(exp)

        payload.save_to_mlflow()

        project = self.fake_mlflow.root / "job" / "job-1" / "project_code_root"
        self.assertEqual((project / "train.py").read_text(), "import torch")
        self.assertEqual((project / "data" / "config.yaml").read_text(), "lr: 0.001")

    def test_load_restores_payload_fields(self) -> None:
        exp = _make_experiment()
        payload = self._make_payload(exp)
        payload.save_to_mlflow()

        loaded = Payload.load_from_mlflow(exp, "job-1")

        self.assertEqual(loaded.experiment, exp)
        self.assertEqual(loaded.job_id, "job-1")
        self.assertEqual(loaded.fn(5), 10)
        self.assertEqual(loaded.args, (42,))

    def test_load_sets_project_code_root_to_downloaded_path(self) -> None:
        exp = _make_experiment()
        payload = self._make_payload(exp)
        payload.save_to_mlflow()

        loaded = Payload.load_from_mlflow(exp, "job-1")

        self.assertTrue(Path(loaded.project_code_root).is_dir())
        self.assertTrue((Path(loaded.project_code_root) / "pyproject.toml").exists())
        self.assertTrue((Path(loaded.project_code_root) / "train.py").exists())
        self.assertTrue((Path(loaded.project_code_root) / "data" / "config.yaml").exists())

    def test_load_project_code_root_differs_from_original(self) -> None:
        exp = _make_experiment()
        payload = self._make_payload(exp)
        payload.save_to_mlflow()

        loaded = Payload.load_from_mlflow(exp, "job-1")

        self.assertNotEqual(loaded.project_code_root, str(self.project_dir))


class TestGetJobStatus(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()
        self.fake_mlflow = FakeMLflow()
        patchers = [
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        clear_instance()

    def test_get_job_status_returns_lifecycle(self) -> None:
        exp = _make_experiment()
        set_instance(exp)
        lifecycle = JobLifecycle(experiment=exp, job_id="job-123", status=JobStatus.RUNNING, retry=True)
        lifecycle.save_to_mlflow()

        result = cortexflow.get_job_status(exp, "job-123")

        self.assertIsInstance(result, JobLifecycle)
        self.assertEqual(result.status, JobStatus.RUNNING)
        self.assertTrue(result.retry)

    def test_get_job_status_returns_error_on_failure(self) -> None:
        exp = _make_experiment()
        set_instance(exp)
        lifecycle = JobLifecycle(experiment=exp, job_id="job-456", status=JobStatus.FAILED, error="OOM killed")
        lifecycle.save_to_mlflow()

        result = cortexflow.get_job_status(exp, "job-456")

        self.assertEqual(result.status, JobStatus.FAILED)
        self.assertEqual(result.error, "OOM killed")


if __name__ == "__main__":
    unittest.main()
