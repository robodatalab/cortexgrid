from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cloudpickle  # type: ignore

import cortexflow
from cortexflow.experiment import Experiment, clear_instance, set_instance
from cortexflow.ray_util import JobStatus, get_ray_job_status
from cortexflow.jobs import (
    JobLifecycle,
    LifecycleEvent,
    Payload,
    list_experiment_run_jobs,
    stop_experiment_run_jobs,
)


EXPERIMENT_NAME = "exp"
RUN_ID = "run-1"


def _make_experiment() -> Experiment:
    return Experiment(experiment_name=EXPERIMENT_NAME, run_id=RUN_ID)


class FakeMLflow:
    """Fake MlflowClient backed by a real temp directory."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def log_artifact(
        self, run_id: str, local_path: str, artifact_path: str = ""
    ) -> None:
        dest = self.root / artifact_path
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest / Path(local_path).name)

    def log_artifacts(
        self, run_id: str, local_dir: str, artifact_path: str = ""
    ) -> None:
        dest = self.root / artifact_path
        shutil.copytree(local_dir, str(dest), dirs_exist_ok=True)

    def download_artifacts(self, run_id: str, path: str) -> str:
        return str(self.root / path)

    def list_artifacts(self, run_id: str, path: str = "") -> list:
        target = self.root / path
        if not target.exists():
            return []
        return [
            SimpleNamespace(path=f"{path}/{d.name}", is_dir=d.is_dir())
            for d in target.iterdir()
        ]


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
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexflow.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch(
                "cortexflow.jobs.subprocess.run",
                return_value=MagicMock(
                    stdout="numpy==1.26\ntorch==2.5\ncortexflow @ git+https://github.com/paksas/robolab-infra.git@abc123\n"
                ),
            ),
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
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        self.assertTrue(
            (
                self.fake_mlflow.root
                / "job"
                / job_id
                / "project_code_root"
                / "payload.pkl"
            ).exists()
        )
        self.assertTrue(
            (self.fake_mlflow.root / "job" / job_id / "lifecycle.json").exists()
        )

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

        requirements = (
            self.fake_mlflow.root
            / "job"
            / job_id
            / "project_code_root"
            / "requirements.txt"
        )
        self.assertTrue(requirements.exists())
        self.assertIn("numpy==1.26", requirements.read_text())
        self.assertIn("torch==2.5", requirements.read_text())

    def test_remote_injects_github_token_into_git_urls(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        requirements = (
            self.fake_mlflow.root
            / "job"
            / job_id
            / "project_code_root"
            / "requirements.txt"
        )
        content = requirements.read_text()
        self.assertIn(
            "git+https://x-access-token:ghp_faketoken@github.com/paksas/robolab-infra.git",
            content,
        )
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
        self.assertFalse(lifecycle.stop_requested)
        self.assertEqual(lifecycle.history, [])
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

        raw = (
            self.fake_mlflow.root / "job" / job_id / "project_code_root" / "payload.pkl"
        ).read_bytes()
        payload: Payload = cloudpickle.loads(raw)
        self.assertIsNotNone(payload.fn)
        self.assertEqual(payload.experiment_name, EXPERIMENT_NAME)
        self.assertEqual(payload.run_id, RUN_ID)

    def test_payload_includes_resource_requests(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None, num_gpus=2, num_cpus=4)

        raw = (
            self.fake_mlflow.root / "job" / job_id / "project_code_root" / "payload.pkl"
        ).read_bytes()
        payload: Payload = cloudpickle.loads(raw)
        self.assertEqual(payload.num_gpus, 2)
        self.assertEqual(payload.num_cpus, 4)


class TestPayloadSaveLoad(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        self.project_dir = Path(tempfile.mkdtemp())
        (self.project_dir / "pyproject.toml").write_text("[project]\nname='test'\n")
        (self.project_dir / "train.py").write_text("import torch")
        (self.project_dir / "data").mkdir()
        (self.project_dir / "data" / "config.yaml").write_text("lr: 0.001")

        patchers = [
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexflow.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch(
                "cortexflow.jobs.subprocess.run",
                return_value=MagicMock(stdout="numpy==1.26\n"),
            ),
            patch("cortexflow.jobs.get_secret", return_value="ghp_faketoken"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, str(self.project_dir), True)

    def _make_payload(self, job_id: str = "job-1") -> Payload:
        return Payload(
            experiment_name=EXPERIMENT_NAME,
            run_id=RUN_ID,
            job_id=job_id,
            fn=lambda x: x * 2,
            args=(42,),
            kwargs={},
            project_code_root=str(self.project_dir),
        )

    def test_save_creates_payload_pkl(self) -> None:
        payload = self._make_payload()

        payload.save_to_mlflow()

        self.assertTrue(
            (
                self.fake_mlflow.root
                / "job"
                / "job-1"
                / "project_code_root"
                / "payload.pkl"
            ).exists()
        )

    def test_save_creates_project_code_root_dir(self) -> None:
        payload = self._make_payload()

        payload.save_to_mlflow()

        project = self.fake_mlflow.root / "job" / "job-1" / "project_code_root"
        self.assertTrue(project.is_dir())

    def test_save_copies_project_files(self) -> None:
        payload = self._make_payload()

        payload.save_to_mlflow()

        project = self.fake_mlflow.root / "job" / "job-1" / "project_code_root"
        self.assertTrue((project / "pyproject.toml").exists())
        self.assertTrue((project / "train.py").exists())
        self.assertTrue((project / "data" / "config.yaml").exists())

    def test_save_preserves_file_contents(self) -> None:
        payload = self._make_payload()

        payload.save_to_mlflow()

        project = self.fake_mlflow.root / "job" / "job-1" / "project_code_root"
        self.assertEqual((project / "train.py").read_text(), "import torch")
        self.assertEqual((project / "data" / "config.yaml").read_text(), "lr: 0.001")

    def test_load_restores_payload_fields(self) -> None:
        payload = self._make_payload()
        payload.save_to_mlflow()

        loaded = Payload.load_from_mlflow(RUN_ID, "job-1")

        self.assertEqual(loaded.experiment_name, EXPERIMENT_NAME)
        self.assertEqual(loaded.run_id, RUN_ID)
        self.assertEqual(loaded.job_id, "job-1")
        self.assertEqual(loaded.fn(5), 10)
        self.assertEqual(loaded.args, (42,))

    def test_load_sets_project_code_root_to_downloaded_path(self) -> None:
        payload = self._make_payload()
        payload.save_to_mlflow()

        loaded = Payload.load_from_mlflow(RUN_ID, "job-1")

        self.assertTrue(Path(loaded.project_code_root).is_dir())
        self.assertTrue((Path(loaded.project_code_root) / "pyproject.toml").exists())
        self.assertTrue((Path(loaded.project_code_root) / "train.py").exists())
        self.assertTrue(
            (Path(loaded.project_code_root) / "data" / "config.yaml").exists()
        )

    def test_load_project_code_root_differs_from_original(self) -> None:
        payload = self._make_payload()
        payload.save_to_mlflow()

        loaded = Payload.load_from_mlflow(RUN_ID, "job-1")

        self.assertNotEqual(loaded.project_code_root, str(self.project_dir))


class TestGetJobStatus(unittest.TestCase):
    """``get_ray_job_status`` is a thin mapping from Ray's raw state string.

    ``None`` is the sentinel for "never submitted" and is the only input
    that maps to ``PENDING``. Ray's own PENDING (queued) is folded into
    ``RUNNING`` — once Ray owns the submission, the control plane does
    not distinguish queued from running.
    """

    def setUp(self) -> None:
        self.mock_get_ray_status = MagicMock()
        patcher = patch("cortexflow.ray_util.get_ray_status", self.mock_get_ray_status)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_none_ray_job_id_is_pending(self) -> None:
        self.mock_get_ray_status.return_value = None
        self.assertEqual(get_ray_job_status(None), JobStatus.PENDING)

    def test_ray_running_maps_to_running(self) -> None:
        self.mock_get_ray_status.return_value = "RUNNING"
        self.assertEqual(get_ray_job_status("sid"), JobStatus.RUNNING)
        self.mock_get_ray_status.assert_called_once_with("sid")

    def test_ray_succeeded_maps_to_finished(self) -> None:
        self.mock_get_ray_status.return_value = "SUCCEEDED"
        self.assertEqual(get_ray_job_status("sid"), JobStatus.FINISHED)

    def test_ray_failed_maps_to_failed(self) -> None:
        self.mock_get_ray_status.return_value = "FAILED"
        self.assertEqual(get_ray_job_status("sid"), JobStatus.FAILED)

    def test_ray_stopped_maps_to_stopped(self) -> None:
        self.mock_get_ray_status.return_value = "STOPPED"
        self.assertEqual(get_ray_job_status("sid"), JobStatus.STOPPED)

    def test_ray_pending_maps_to_running(self) -> None:
        self.mock_get_ray_status.return_value = "PENDING"
        self.assertEqual(get_ray_job_status("sid"), JobStatus.RUNNING)

    def test_unknown_ray_state_falls_through_to_running(self) -> None:
        self.mock_get_ray_status.return_value = "SOMETHING_ELSE"
        self.assertEqual(get_ray_job_status("sid"), JobStatus.RUNNING)


class TestListExperimentRunJobs(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        patchers = [
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexflow.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_list_experiment_run_jobs_returns_all_jobs(self) -> None:
        for job_id in ("j1", "j2"):
            JobLifecycle(
                experiment_name=EXPERIMENT_NAME,
                run_id=RUN_ID,
                job_id=job_id,
            ).save_to_mlflow()

        result = list_experiment_run_jobs(RUN_ID)

        self.assertEqual(sorted(j.job_id for j in result), ["j1", "j2"])

    def test_list_experiment_run_jobs_skips_jobs_without_lifecycle(self) -> None:
        JobLifecycle(
            experiment_name=EXPERIMENT_NAME,
            run_id=RUN_ID,
            job_id="j1",
        ).save_to_mlflow()
        # create a job dir without a lifecycle.json
        (self.fake_mlflow.root / "job" / "j2").mkdir(parents=True, exist_ok=True)

        result = list_experiment_run_jobs(RUN_ID)

        self.assertEqual([j.job_id for j in result], ["j1"])

    def test_event_error_round_trips_through_mlflow(self) -> None:
        """An event with an error message survives save+load."""
        saved = JobLifecycle(
            experiment_name=EXPERIMENT_NAME,
            run_id=RUN_ID,
            job_id="j1",
            history=[
                LifecycleEvent(
                    attempt=0,
                    state="pending",
                    start="2026-04-15T10:00:00+00:00",
                    error="payload download failed",
                ),
            ],
        )
        saved.save_to_mlflow()

        loaded = JobLifecycle.load_from_mlflow(RUN_ID, "j1")

        self.assertEqual(len(loaded.history), 1)
        self.assertEqual(loaded.history[0].error, "payload download failed")


class TestGetRayJobId(unittest.TestCase):
    """``JobLifecycle.get_ray_job_id`` accepts a pre-fetched Ray submission
    list so callers can batch the Ray round-trip across many jobs."""

    def _lifecycle(self, job_id: str = "job-1") -> JobLifecycle:
        return JobLifecycle(
            experiment_name=EXPERIMENT_NAME, run_id=RUN_ID, job_id=job_id
        )

    def test_passed_list_is_used_without_querying_ray(self) -> None:
        lifecycle = self._lifecycle()
        with patch(
            "cortexflow.ray_util.list_ray_jobs_with_submission_id"
        ) as mock_list:
            result = lifecycle.get_ray_job_id(
                ["run-1-job-1-0", "run-1-job-1-1"]
            )

        self.assertEqual(result, "run-1-job-1-1")
        mock_list.assert_not_called()

    def test_empty_passed_list_returns_none_without_querying_ray(self) -> None:
        lifecycle = self._lifecycle()
        with patch(
            "cortexflow.ray_util.list_ray_jobs_with_submission_id"
        ) as mock_list:
            result = lifecycle.get_ray_job_id([])

        self.assertIsNone(result)
        mock_list.assert_not_called()

    def test_no_arg_queries_ray_live(self) -> None:
        lifecycle = self._lifecycle()
        with patch(
            "cortexflow.ray_util.list_ray_jobs_with_submission_id",
            return_value=["run-1-job-1-0"],
        ) as mock_list:
            result = lifecycle.get_ray_job_id()

        self.assertEqual(result, "run-1-job-1-0")
        mock_list.assert_called_once()


class TestStopExperimentRunJobs(unittest.TestCase):
    """stop_experiment_run_jobs is a pure latch-flipper: it never calls Ray."""

    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        patchers = [
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexflow.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _save_job(
        self,
        job_id: str,
        stop_requested: bool = False,
    ) -> None:
        JobLifecycle(
            experiment_name=EXPERIMENT_NAME,
            run_id=RUN_ID,
            job_id=job_id,
            stop_requested=stop_requested,
        ).save_to_mlflow()

    def _loaded(self, job_id: str) -> JobLifecycle:
        return JobLifecycle.load_from_mlflow(RUN_ID, job_id)

    def test_flips_stop_requested_on_unsubmitted_job(self) -> None:
        self._save_job("j1")

        stop_experiment_run_jobs(RUN_ID)

        self.assertTrue(self._loaded("j1").stop_requested)

    def test_flips_stop_requested_on_submitted_job(self) -> None:
        self._save_job("j2")

        stop_experiment_run_jobs(RUN_ID)

        loaded = self._loaded("j2")
        self.assertTrue(loaded.stop_requested)

    def test_does_not_rewrite_already_requested_job(self) -> None:
        self._save_job("j3", stop_requested=True)
        # Capture the original mtime-equivalent by snapshotting the file.
        original = (self.fake_mlflow.root / "job" / "j3" / "lifecycle.json").read_text()

        stop_experiment_run_jobs(RUN_ID)

        loaded = self._loaded("j3")
        self.assertTrue(loaded.stop_requested)
        # File content unchanged — we short-circuited before save_to_mlflow.
        after = (self.fake_mlflow.root / "job" / "j3" / "lifecycle.json").read_text()
        self.assertEqual(original, after)

    def test_mixed_jobs_all_get_flipped_except_already_requested(self) -> None:
        self._save_job("pending")
        self._save_job("running")
        self._save_job("requested", stop_requested=True)

        stop_experiment_run_jobs(RUN_ID)

        self.assertTrue(self._loaded("pending").stop_requested)
        self.assertTrue(self._loaded("running").stop_requested)
        self.assertTrue(self._loaded("requested").stop_requested)


if __name__ == "__main__":
    unittest.main()
