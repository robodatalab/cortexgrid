from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cloudpickle  # type: ignore

import cortexgrid
from cortexgrid._bundle import BundleDesc
from cortexgrid.experiment import Experiment, clear_instance, set_instance
from cortexgrid.ray_util import JobStatus, get_ray_job_status
from cortexgrid.jobs import (
    JobFailed,
    JobFuture,
    JobLifecycle,
    JobResult,
    JobResultUnavailable,
    LifecycleEvent,
    Payload,
    delete_job,
    list_experiment_run_jobs,
    purge_abandoned_job,
    purge_ray_job,
    stop_experiment_run_jobs,
    wait_for_job_result,
)

from tests.fakes import FakeRay


EXPERIMENT_NAME = "exp"
RUN_ID = "run-1"

# Real repo files, pinned as `bundle`'s output so the job path is tested in
# isolation from (slow) real import-graph tracing. `stage` lays them out at
# their import paths, so the uploaded tarball mirrors the repo layout.
_REPO = Path(__file__).resolve().parents[3]
_SHIPPED = {
    _REPO / "cortexgrid/__init__.py",
    _REPO / "cortexgrid/experiment.py",
    _REPO / "cortexgrid/jobs.py",
    _REPO / "cortexgrid/ray_util.py",
    _REPO / "tests/__init__.py",
    _REPO / "tests/unit/__init__.py",
    _REPO / "tests/unit/cortexgrid/__init__.py",
    _REPO / "tests/unit/cortexgrid/test_jobs.py",
}


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

    def get_run(self, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            info=SimpleNamespace(run_id=run_id, artifact_uri=f"fake://{run_id}")
        )


class FakeS3:
    """Fake s3_util backed by a temp directory."""

    BUCKET = "canonical"

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def upload(self, local_path: str, dest_path: str | None = None) -> str:
        dest_path = dest_path or Path(local_path).name
        dest = self.root / self.BUCKET / dest_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)
        return f"s3://{self.BUCKET}/{dest_path}"

    def download(self, src_path: str, local_path: str | None = None) -> str:
        local_path = local_path or Path(src_path).name
        shutil.copy2(self.root / self.BUCKET / src_path, local_path)
        return local_path


def _extract_uploaded_project(fake_mlflow: FakeMLflow, fake_s3: FakeS3, job_id: str) -> Path:
    """Read the job's manifest from FakeMLflow, extract its tarball from FakeS3."""
    manifest = json.loads((fake_mlflow.root / "job" / job_id / "manifest.json").read_text())
    bucket, _, key = manifest["code_tarball_uri"].removeprefix("s3://").partition("/")
    extract_dir = Path(tempfile.mkdtemp())
    with tarfile.open(fake_s3.root / bucket / key, "r:gz") as tar:
        tar.extractall(extract_dir)
    return extract_dir / "project_code_root"


class TestRemote(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()
        self.fake_mlflow = FakeMLflow()
        self.fake_s3 = FakeS3()

        patchers = [
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("cortexgrid.jobs.s3_util", self.fake_s3),
            # Bundling is exercised in test_bundle; pin it here so the job path
            # is tested in isolation, without tracing all of torch/mlflow.
            patch(
                "cortexgrid.jobs.bundle",
                return_value=BundleDesc(local_files=set(_SHIPPED), tp_deps={}),
            ),
            patch("cortexgrid.jobs.worker_provides", return_value=frozenset()),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        clear_instance()

    def test_remote_returns_future_carrying_job_identity(self) -> None:
        set_instance(_make_experiment())

        future = cortexgrid.remote(lambda: None)

        self.assertIsInstance(future, JobFuture)
        self.assertEqual(future.experiment_name, EXPERIMENT_NAME)
        self.assertEqual(future.run_id, RUN_ID)
        self.assertTrue(len(future.job_id) > 0)

    def test_remote_uploads_payload_and_lifecycle(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexgrid.remote(lambda: None).job_id

        project_root = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, job_id)
        self.assertTrue((project_root / "payload.pkl").exists())
        self.assertTrue(
            (self.fake_mlflow.root / "job" / job_id / "lifecycle.json").exists()
        )

    def test_remote_uploads_project_code(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexgrid.remote(lambda: None).job_id

        project_root = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, job_id)
        self.assertTrue(
            (project_root / "tests" / "unit" / "cortexgrid" / "test_jobs.py").is_file()
        )
        self.assertTrue((project_root / "tests" / "__init__.py").is_file())
        self.assertTrue((project_root / "tests" / "unit" / "__init__.py").is_file())
        self.assertTrue(
            (project_root / "tests" / "unit" / "cortexgrid" / "__init__.py").is_file()
        )
        self.assertTrue((project_root / "cortexgrid" / "__init__.py").is_file())
        self.assertTrue((project_root / "cortexgrid" / "experiment.py").is_file())
        self.assertTrue((project_root / "cortexgrid" / "jobs.py").is_file())
        self.assertTrue((project_root / "cortexgrid" / "ray_util.py").is_file())
        self.assertFalse((project_root / ".venv").exists())

    def test_remote_ships_no_requirements_txt(self) -> None:
        # Third-party requirements travel on the lifecycle, not in the tarball.
        set_instance(_make_experiment())

        job_id = cortexgrid.remote(lambda: None).job_id

        project_root = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, job_id)
        self.assertFalse((project_root / "requirements.txt").exists())

    def test_initial_lifecycle_is_pending(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexgrid.remote(lambda: None).job_id

        raw = (self.fake_mlflow.root / "job" / job_id / "lifecycle.json").read_text()
        lifecycle = JobLifecycle.from_json(raw)
        self.assertFalse(lifecycle.stop_requested)
        self.assertEqual(lifecycle.history, [])
        self.assertFalse(lifecycle.retry)

    def test_lifecycle_includes_retry_flag(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexgrid.remote(lambda: None, retry=True).job_id

        raw = (self.fake_mlflow.root / "job" / job_id / "lifecycle.json").read_text()
        lifecycle = JobLifecycle.from_json(raw)
        self.assertTrue(lifecycle.retry)

    def test_lifecycle_carries_pip_requirements_the_worker_lacks(self) -> None:
        set_instance(_make_experiment())
        desc = BundleDesc(
            local_files=set(_SHIPPED), tp_deps={"tqdm": "4.67.3", "ray": "2.55.1"}
        )

        with (
            patch("cortexgrid.jobs.bundle", return_value=desc),
            patch("cortexgrid.jobs.worker_provides", return_value=frozenset({"ray"})),
        ):
            job_id = cortexgrid.remote(lambda: None).job_id

        raw = (self.fake_mlflow.root / "job" / job_id / "lifecycle.json").read_text()
        self.assertEqual(JobLifecycle.from_json(raw).pip_requirements, ["tqdm==4.67.3"])

    def test_lifecycle_saved_without_pip_requirements_loads_with_none(self) -> None:
        # Lifecycles written before pip requirements existed lack the field.
        raw = json.dumps({"experiment_name": EXPERIMENT_NAME, "run_id": RUN_ID, "job_id": "j"})

        self.assertEqual(JobLifecycle.from_json(raw).pip_requirements, [])

    def test_payload_round_trips_through_cloudpickle(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexgrid.remote(lambda: None).job_id

        project_root = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, job_id)
        payload: Payload = cloudpickle.loads((project_root / "payload.pkl").read_bytes())
        self.assertIsNotNone(payload.fn)
        self.assertEqual(payload.experiment_name, EXPERIMENT_NAME)
        self.assertEqual(payload.run_id, RUN_ID)

    def test_payload_includes_resource_requests(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexgrid.remote(lambda: None, num_gpus=2, num_cpus=4).job_id

        project_root = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, job_id)
        payload: Payload = cloudpickle.loads((project_root / "payload.pkl").read_bytes())
        self.assertEqual(payload.num_gpus, 2)
        self.assertEqual(payload.num_cpus, 4)


class TestPayloadSaveLoad(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        self.fake_s3 = FakeS3()
        self.project_dir = Path(tempfile.mkdtemp())
        (self.project_dir / "pyproject.toml").write_text("[project]\nname='test'\n")
        (self.project_dir / "train.py").write_text("import torch")
        (self.project_dir / "data").mkdir()
        (self.project_dir / "data" / "config.yaml").write_text("lr: 0.001")

        patchers = [
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("cortexgrid.jobs.s3_util", self.fake_s3),
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

        project = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, "job-1")
        self.assertTrue((project / "payload.pkl").exists())

    def test_save_creates_project_code_root_dir(self) -> None:
        payload = self._make_payload()

        payload.save_to_mlflow()

        project = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, "job-1")
        self.assertTrue(project.is_dir())

    def test_save_copies_project_files(self) -> None:
        payload = self._make_payload()

        payload.save_to_mlflow()

        project = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, "job-1")
        self.assertTrue((project / "pyproject.toml").exists())
        self.assertTrue((project / "train.py").exists())
        self.assertTrue((project / "data" / "config.yaml").exists())

    def test_save_preserves_file_contents(self) -> None:
        payload = self._make_payload()

        payload.save_to_mlflow()

        project = _extract_uploaded_project(self.fake_mlflow, self.fake_s3, "job-1")
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
        patcher = patch("cortexgrid.ray_util.get_ray_status", self.mock_get_ray_status)
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
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
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
            "cortexgrid.ray_util.list_ray_jobs_with_submission_id"
        ) as mock_list:
            result = lifecycle.get_ray_job_id(
                ["run-1-job-1-0", "run-1-job-1-1"]
            )

        self.assertEqual(result, "run-1-job-1-1")
        mock_list.assert_not_called()

    def test_empty_passed_list_returns_none_without_querying_ray(self) -> None:
        lifecycle = self._lifecycle()
        with patch(
            "cortexgrid.ray_util.list_ray_jobs_with_submission_id"
        ) as mock_list:
            result = lifecycle.get_ray_job_id([])

        self.assertIsNone(result)
        mock_list.assert_not_called()

    def test_no_arg_queries_ray_live(self) -> None:
        lifecycle = self._lifecycle()
        with patch(
            "cortexgrid.ray_util.list_ray_jobs_with_submission_id",
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
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
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


class FakeArtifactRepo:
    """Stand-in for mlflow's artifact repository, over FakeMLflow's tree."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def delete_artifacts(self, artifact_path: str = "") -> None:
        shutil.rmtree(self.root / artifact_path, ignore_errors=True)


class FakeS3Packages:
    """Stand-in for the s3_util module: records what was wiped."""

    def __init__(self) -> None:
        self.deleted_prefixes: list[str] = []

    def delete_prefix(self, prefix: str) -> None:
        self.deleted_prefixes.append(prefix)


class LatchWatchingRay(FakeRay):
    """Records the job's stop_requested latch as each attempt is stopped."""

    def __init__(self, jobs: dict[str, str], job_id: str) -> None:
        super().__init__(jobs)
        self.job_id = job_id
        self.latch_at_stop: list[bool] = []

    def stop_job(self, submission_id: str) -> None:
        self.latch_at_stop.append(
            JobLifecycle.load_from_mlflow(RUN_ID, self.job_id).stop_requested
        )
        super().stop_job(submission_id)


class StubbornRay(FakeRay):
    """A ray whose jobs keep running for `settles_after` status reads."""

    def __init__(self, jobs: dict[str, str], settles_after: int) -> None:
        super().__init__(jobs)
        self.settles_after = settles_after
        self.status_reads = 0

    def get_job_status(self, submission_id: str) -> Any:
        self.status_reads += 1
        if self.status_reads <= self.settles_after:
            return SimpleNamespace(value="RUNNING")
        return super().get_job_status(submission_id)


class _JobTeardownTest(unittest.TestCase):
    """Shared rig: a temp-dir MLflow, a fake Ray and a recording s3_util."""

    ray: FakeRay

    def _patch_infra(self, ray: FakeRay) -> None:
        self.fake_mlflow = FakeMLflow()
        self.fake_s3 = FakeS3Packages()
        self.ray = ray
        patchers = [
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("cortexgrid.jobs.s3_util", self.fake_s3),
            patch(
                "cortexgrid.jobs.get_artifact_repository",
                side_effect=lambda *a, **k: FakeArtifactRepo(self.fake_mlflow.root),
            ),
            patch(
                "cortexgrid.ray_util.get_ray_job_submission_client",
                return_value=ray,
            ),
            patch("cortexgrid.jobs.time.sleep", return_value=None),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _save_job(self, job_id: str, stop_requested: bool = False) -> None:
        JobLifecycle(
            experiment_name=EXPERIMENT_NAME,
            run_id=RUN_ID,
            job_id=job_id,
            stop_requested=stop_requested,
        ).save_to_mlflow()


class TestDeleteJob(_JobTeardownTest):
    """delete_job leaves nothing behind that could resurrect the job."""

    def test_deleted_job_is_no_longer_listed_for_the_run(self) -> None:
        self._patch_infra(FakeRay())
        self._save_job("j1")
        self._save_job("j2")

        delete_job(RUN_ID, "j1")

        self.assertEqual(
            [j.job_id for j in list_experiment_run_jobs(RUN_ID)], ["j2"]
        )

    def test_every_ray_attempt_of_the_job_is_purged(self) -> None:
        self._patch_infra(
            FakeRay({"run-1-j1-0": "FAILED", "run-1-j1-1": "RUNNING"})
        )
        self._save_job("j1")

        delete_job(RUN_ID, "j1")

        self.assertEqual(list(self.ray.jobs), [])

    def test_ray_attempts_of_other_jobs_are_untouched(self) -> None:
        self._patch_infra(
            FakeRay({"run-1-j1-0": "RUNNING", "run-1-j2-0": "RUNNING"})
        )
        self._save_job("j1")
        self._save_job("j2")

        delete_job(RUN_ID, "j1")

        self.assertEqual(list(self.ray.jobs), ["run-1-j2-0"])

    def test_job_package_is_wiped_from_s3(self) -> None:
        self._patch_infra(FakeRay())
        self._save_job("j1")

        delete_job(RUN_ID, "j1")

        self.assertEqual(self.fake_s3.deleted_prefixes, ["job/j1/"])

    def test_stop_is_requested_before_any_ray_attempt_is_touched(self) -> None:
        """A retry job must not be re-submitted while it is being torn down."""
        ray = LatchWatchingRay({"run-1-j1-0": "RUNNING"}, "j1")
        self._patch_infra(ray)
        self._save_job("j1")

        delete_job(RUN_ID, "j1")

        self.assertEqual(ray.latch_at_stop, [True])

    def test_deleting_a_job_that_is_not_there_is_an_error(self) -> None:
        self._patch_infra(FakeRay())

        with self.assertRaises(FileNotFoundError):
            delete_job(RUN_ID, "never-existed")


class TestPurgeAbandonedJob(_JobTeardownTest):
    """A job whose lifecycle is gone is only its Ray attempts."""

    def test_every_attempt_of_the_job_is_purged(self) -> None:
        self._patch_infra(
            FakeRay({"run-1-j1-0": "FAILED", "run-1-j1-1": "RUNNING"})
        )

        purge_abandoned_job(RUN_ID, "j1")

        self.assertEqual(list(self.ray.jobs), [])

    def test_attempts_of_other_jobs_are_untouched(self) -> None:
        self._patch_infra(
            FakeRay({"run-1-j1-0": "RUNNING", "run-1-j2-0": "RUNNING"})
        )

        purge_abandoned_job(RUN_ID, "j1")

        self.assertEqual(list(self.ray.jobs), ["run-1-j2-0"])

    def test_nothing_to_purge_is_not_an_error(self) -> None:
        self._patch_infra(FakeRay())

        purge_abandoned_job(RUN_ID, "j1")

        self.assertEqual(list(self.ray.jobs), [])


class TestPurgeRayJob(_JobTeardownTest):
    """purge_ray_job is for submissions no cortexgrid job claims any more."""

    def test_stopped_job_is_deleted_from_rays_store(self) -> None:
        self._patch_infra(FakeRay({"orphan-0": "RUNNING"}))

        purge_ray_job("orphan-0")

        self.assertEqual(list(self.ray.jobs), [])

    def test_waits_for_the_job_to_settle_before_deleting_it(self) -> None:
        ray = StubbornRay({"orphan-0": "RUNNING"}, settles_after=2)
        self._patch_infra(ray)

        purge_ray_job("orphan-0")

        self.assertGreater(ray.status_reads, 2)
        self.assertEqual(list(ray.jobs), [])

    def test_job_that_never_settles_is_reported_and_left_alone(self) -> None:
        ray = StubbornRay({"orphan-0": "RUNNING"}, settles_after=10**6)
        self._patch_infra(ray)

        with patch("cortexgrid.jobs._RAY_PURGE_TIMEOUT_S", 0):
            with self.assertRaises(TimeoutError):
                purge_ray_job("orphan-0")

        self.assertEqual(list(ray.jobs), ["orphan-0"])


if __name__ == "__main__":
    unittest.main()


def _payload(job_id: str = "job-1") -> Payload:
    return Payload(
        experiment_name=EXPERIMENT_NAME,
        run_id=RUN_ID,
        job_id=job_id,
        fn=lambda: None,
        args=(),
        kwargs={},
        project_code_root="/tmp/does-not-matter",
    )


class _JobError(RuntimeError):
    """A user-defined exception, to check the original type survives the trip."""


class TestJobResult(unittest.TestCase):
    """The driver records what the job's function produced; the client side
    turns that back into a value or an exception."""

    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        patchers = [
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_value_round_trips_through_mlflow(self) -> None:
        JobResult.from_value(_payload(), {"loss": 0.5}).save_to_mlflow()

        loaded = JobResult.load_from_mlflow(RUN_ID, "job-1")

        self.assertEqual(loaded.unwrap(), {"loss": 0.5})

    def test_result_lands_beside_the_payload(self) -> None:
        JobResult.from_value(_payload(), 1).save_to_mlflow()

        self.assertTrue(
            (self.fake_mlflow.root / "job" / "job-1" / "result.pkl").exists()
        )

    def test_none_return_value_round_trips(self) -> None:
        JobResult.from_value(_payload(), None).save_to_mlflow()

        self.assertIsNone(JobResult.load_from_mlflow(RUN_ID, "job-1").unwrap())

    def test_missing_result_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            JobResult.load_from_mlflow(RUN_ID, "job-1")

    def test_unpicklable_value_still_records_a_finished_job(self) -> None:
        # A job whose return value cannot be pickled must still finish; only
        # the value is lost.
        result = JobResult.from_value(_payload(), (i for i in range(3)))

        self.assertTrue(result.ok)
        self.assertIsNone(result.value_pickle)
        self.assertIn("generator", result.value_error or "")

    def test_unpicklable_value_raises_job_result_unavailable(self) -> None:
        JobResult.from_value(_payload(), (i for i in range(3))).save_to_mlflow()

        loaded = JobResult.load_from_mlflow(RUN_ID, "job-1")

        with self.assertRaises(JobResultUnavailable):
            loaded.unwrap()

    def test_exception_is_re_raised_with_its_original_type(self) -> None:
        JobResult.from_exception(_payload(), _JobError("bad batch")).save_to_mlflow()

        loaded = JobResult.load_from_mlflow(RUN_ID, "job-1")

        with self.assertRaises(_JobError) as caught:
            loaded.unwrap()
        self.assertEqual(str(caught.exception), "bad batch")

    def test_re_raised_exception_carries_the_remote_traceback_as_its_cause(self) -> None:
        try:
            raise _JobError("bad batch")
        except _JobError as exc:
            JobResult.from_exception(_payload(), exc).save_to_mlflow()

        loaded = JobResult.load_from_mlflow(RUN_ID, "job-1")

        with self.assertRaises(_JobError) as caught:
            loaded.unwrap()
        cause = caught.exception.__cause__
        self.assertIsInstance(cause, JobFailed)
        self.assertIn("job job-1 failed", str(cause))
        self.assertIn("_JobError: bad batch", str(cause))

    def test_unpicklable_exception_falls_back_to_job_failed(self) -> None:
        JobResult.from_exception(
            _payload(), _JobError(threading.Lock())
        ).save_to_mlflow()

        loaded = JobResult.load_from_mlflow(RUN_ID, "job-1")

        self.assertIsNone(loaded.exception_pickle)
        with self.assertRaises(JobFailed) as caught:
            loaded.unwrap()
        self.assertIn("_JobError", str(caught.exception))


class TestWaitForJobResult(unittest.TestCase):
    """Waiting polls the lifecycle until Ray reports a terminal state, then
    hands back whatever the driver recorded."""

    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        patchers = [
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("cortexgrid.ray_util.list_ray_jobs_with_submission_id", return_value=[]),
            patch("cortexgrid.jobs.time.sleep"),
        ]
        self.mocks = [p.start() for p in patchers]
        for p in patchers:
            self.addCleanup(p.stop)
        self.sleep = self.mocks[-1]

    def _save_lifecycle(self, retry: bool = False, error: str | None = None) -> None:
        history = (
            [
                LifecycleEvent(
                    attempt=0,
                    state="pending",
                    start="2026-04-15T10:00:00+00:00",
                    error=error,
                )
            ]
            if error
            else []
        )
        JobLifecycle(
            experiment_name=EXPERIMENT_NAME,
            run_id=RUN_ID,
            job_id="job-1",
            retry=retry,
            history=history,
        ).save_to_mlflow()

    def test_returns_the_functions_value(self) -> None:
        self._save_lifecycle()
        JobResult.from_value(_payload(), {"loss": 0.5}).save_to_mlflow()

        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.FINISHED
        ):
            self.assertEqual(wait_for_job_result(RUN_ID, "job-1"), {"loss": 0.5})

    def test_raises_what_the_function_raised(self) -> None:
        self._save_lifecycle()
        JobResult.from_exception(_payload(), _JobError("bad batch")).save_to_mlflow()

        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.FAILED
        ):
            with self.assertRaises(_JobError):
                wait_for_job_result(RUN_ID, "job-1")

    def test_polls_until_the_job_reaches_a_terminal_state(self) -> None:
        self._save_lifecycle()
        JobResult.from_value(_payload(), 7).save_to_mlflow()

        with patch(
            "cortexgrid.jobs.get_ray_job_status",
            side_effect=[JobStatus.PENDING, JobStatus.RUNNING, JobStatus.FINISHED],
        ):
            self.assertEqual(wait_for_job_result(RUN_ID, "job-1"), 7)
        self.assertEqual(self.sleep.call_count, 2)

    def test_finished_without_a_recorded_result_is_unavailable(self) -> None:
        self._save_lifecycle()

        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.FINISHED
        ):
            with self.assertRaises(JobResultUnavailable):
                wait_for_job_result(RUN_ID, "job-1")

    def test_failure_before_the_function_ran_reports_the_submission_error(self) -> None:
        self._save_lifecycle(error="payload download failed")

        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.FAILED
        ):
            with self.assertRaises(JobFailed) as caught:
                wait_for_job_result(RUN_ID, "job-1")
        self.assertIn("payload download failed", str(caught.exception))

    def test_stopped_job_raises_job_failed(self) -> None:
        self._save_lifecycle()

        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.STOPPED
        ):
            with self.assertRaises(JobFailed) as caught:
                wait_for_job_result(RUN_ID, "job-1")
        self.assertIn("stopped", str(caught.exception))

    def test_retry_job_cannot_be_waited_on(self) -> None:
        # Retries are unbounded by design, so a blocking wait has no end.
        self._save_lifecycle(retry=True)

        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.FAILED
        ):
            with self.assertRaises(ValueError) as caught:
                wait_for_job_result(RUN_ID, "job-1")
        self.assertIn("retry=True", str(caught.exception))

    def test_zero_timeout_gives_up_without_sleeping(self) -> None:
        self._save_lifecycle()

        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.RUNNING
        ):
            with self.assertRaises(TimeoutError):
                wait_for_job_result(RUN_ID, "job-1", timeout=0)
        self.sleep.assert_not_called()


class TestJobFuture(unittest.TestCase):
    """The handle `remote` returns: identity now, status and result on demand."""

    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        patchers = [
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("cortexgrid.ray_util.list_ray_jobs_with_submission_id", return_value=[]),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        JobLifecycle(
            experiment_name=EXPERIMENT_NAME, run_id=RUN_ID, job_id="job-1"
        ).save_to_mlflow()
        self.future = JobFuture(
            experiment_name=EXPERIMENT_NAME, run_id=RUN_ID, job_id="job-1"
        )

    def test_status_reports_the_live_ray_state(self) -> None:
        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.RUNNING
        ):
            self.assertEqual(self.future.status(), JobStatus.RUNNING)

    def test_done_is_false_while_running(self) -> None:
        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.RUNNING
        ):
            self.assertFalse(self.future.done())

    def test_done_is_true_once_terminal(self) -> None:
        for status in (JobStatus.FINISHED, JobStatus.FAILED, JobStatus.STOPPED):
            with self.subTest(status=status):
                with patch(
                    "cortexgrid.jobs.get_ray_job_status", return_value=status
                ):
                    self.assertTrue(self.future.done())

    def test_result_returns_the_functions_value(self) -> None:
        JobResult.from_value(_payload(), "done").save_to_mlflow()

        with patch(
            "cortexgrid.jobs.get_ray_job_status", return_value=JobStatus.FINISHED
        ):
            self.assertEqual(self.future.result(), "done")
