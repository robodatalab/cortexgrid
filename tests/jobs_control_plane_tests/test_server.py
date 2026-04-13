from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import cloudpickle  # type: ignore
from cortexflow.experiment import Experiment
from cortexflow.jobs import JobLifecycle, JobStatus, Payload
from jobs_control_plane.server import poll_once


def _make_experiment() -> Experiment:
    return Experiment(
        experiment_name="exp",
        run_id="run-1",
    )


class FakeMLflow:
    """Fake MlflowClient backed by a real temp directory."""

    def __init__(self) -> None:
        self.artifact_root = Path(tempfile.mkdtemp())

    def list_artifacts(self, run_id: str, path: str = "") -> list[SimpleNamespace]:
        target = self.artifact_root / path
        if not target.exists():
            return []
        return [
            SimpleNamespace(path=f"{path}/{d.name}", is_dir=d.is_dir())
            for d in target.iterdir()
        ]

    def download_artifacts(self, run_id: str, artifact_path: str) -> str:
        return str(self.artifact_root / artifact_path)

    def log_artifact(
        self, run_id: str, local_path: str, artifact_path: str = ""
    ) -> None:
        dest_dir = self.artifact_root / artifact_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest_dir / Path(local_path).name)

    def log_artifacts(
        self, run_id: str, local_dir: str, artifact_path: str = ""
    ) -> None:
        dest = self.artifact_root / artifact_path
        shutil.copytree(local_dir, str(dest), dirs_exist_ok=True)

    def add_job(
        self,
        job_id: str,
        lifecycle: JobLifecycle,
        payload: Payload | None = None,
    ) -> None:
        job_dir = self.artifact_root / "job" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "lifecycle.json").write_text(lifecycle.to_json())
        if payload is not None:
            project_dest = job_dir / "project_code_root"
            shutil.copytree(
                payload.project_code_root,
                str(project_dest),
                dirs_exist_ok=True,
            )
            (project_dest / "payload.pkl").write_bytes(cloudpickle.dumps(payload))


class FakeRay:
    """Fake JobSubmissionClient."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.statuses: dict[str, str] = {}
        self.logs_by_id: dict[str, str] = {}

    def submit_job(self, entrypoint: str, runtime_env: dict, **kwargs: Any) -> str:
        ray_job_id = f"ray_{len(self.submitted)}"
        self.submitted.append(
            {"entrypoint": entrypoint, "runtime_env": runtime_env, **kwargs}
        )
        self.statuses[ray_job_id] = "RUNNING"
        return ray_job_id

    def get_job_status(self, job_id: str) -> SimpleNamespace:
        return SimpleNamespace(value=self.statuses.get(job_id, "PENDING"))

    def get_job_logs(self, job_id: str) -> str:
        return self.logs_by_id.get(job_id, "")


class TestPollOnce(unittest.TestCase):
    def setUp(self) -> None:
        self.exp = _make_experiment()

        self.fake_mlflow = FakeMLflow()
        self.fake_ray = FakeRay()

        self.project_root = Path(tempfile.mkdtemp())
        (self.project_root / "pyproject.toml").write_text("[project]\nname='test'\n")
        self.original_cwd = os.getcwd()
        os.chdir(self.project_root)

        patchers = [
            patch(
                "jobs_control_plane.server.list_experiments", return_value=[self.exp]
            ),
            patch(
                "jobs_control_plane.server.get_ray_job_server_uri",
                return_value="http://test:8265",
            ),
            patch(
                "jobs_control_plane.server.JobSubmissionClient",
                return_value=self.fake_ray,
            ),
            patch(
                "cortexflow.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(os.chdir, self.original_cwd)
        self.addCleanup(shutil.rmtree, self.project_root, True)

    def _make_payload(self) -> Payload:
        return Payload(
            experiment_name=self.exp.experiment_name,
            run_id=self.exp.run_id,
            job_id="job-1",
            fn=lambda: None,
            args=(),
            kwargs={},
            project_code_root=str(self.project_root),
        )

    def _make_lifecycle(self, **overrides: Any) -> JobLifecycle:
        return JobLifecycle(
            experiment_name=self.exp.experiment_name,
            run_id=self.exp.run_id,
            job_id="job-1",
            **overrides,
        )

    def test_pending_job_gets_submitted_to_ray(self) -> None:
        lifecycle = self._make_lifecycle(status=JobStatus.PENDING)
        self.fake_mlflow.add_job("job-1", lifecycle, self._make_payload())

        poll_once()

        self.assertEqual(len(self.fake_ray.submitted), 1)
        updated = JobLifecycle.from_json(
            (
                self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json"
            ).read_text()
        )
        self.assertEqual(updated.status, JobStatus.RUNNING)
        self.assertIsNotNone(updated.ray_job_id)

    def test_running_job_transitions_to_finished_on_success(self) -> None:
        lifecycle = self._make_lifecycle(status=JobStatus.RUNNING, ray_job_id="ray_0")
        self.fake_mlflow.add_job("job-1", lifecycle)
        self.fake_ray.statuses["ray_0"] = "SUCCEEDED"

        poll_once()

        updated = JobLifecycle.from_json(
            (
                self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json"
            ).read_text()
        )
        self.assertEqual(updated.status, JobStatus.FINISHED)

    def test_running_job_transitions_to_failed_on_failure(self) -> None:
        lifecycle = self._make_lifecycle(status=JobStatus.RUNNING, ray_job_id="ray_0")
        self.fake_mlflow.add_job("job-1", lifecycle)
        self.fake_ray.statuses["ray_0"] = "FAILED"
        self.fake_ray.logs_by_id["ray_0"] = "CUDA OOM"

        poll_once()

        updated = JobLifecycle.from_json(
            (
                self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json"
            ).read_text()
        )
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertIn("CUDA OOM", updated.error or "")

    def test_failed_job_with_retry_gets_resubmitted(self) -> None:
        lifecycle = self._make_lifecycle(
            status=JobStatus.FAILED, retry=True, error="OOM"
        )
        self.fake_mlflow.add_job("job-1", lifecycle, self._make_payload())

        poll_once()

        self.assertEqual(len(self.fake_ray.submitted), 1)
        updated = JobLifecycle.from_json(
            (
                self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json"
            ).read_text()
        )
        self.assertEqual(updated.status, JobStatus.RUNNING)

    def test_failed_job_without_retry_stays_failed(self) -> None:
        lifecycle = self._make_lifecycle(
            status=JobStatus.FAILED, retry=False, error="OOM"
        )
        self.fake_mlflow.add_job("job-1", lifecycle)

        poll_once()

        self.assertEqual(len(self.fake_ray.submitted), 0)
        updated = JobLifecycle.from_json(
            (
                self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json"
            ).read_text()
        )
        self.assertEqual(updated.status, JobStatus.FAILED)

    def test_finished_job_is_not_touched(self) -> None:
        lifecycle = self._make_lifecycle(status=JobStatus.FINISHED, ray_job_id="ray_0")
        self.fake_mlflow.add_job("job-1", lifecycle)

        poll_once()

        self.assertEqual(len(self.fake_ray.submitted), 0)

    def test_submitted_job_has_project_as_working_dir(self) -> None:
        lifecycle = self._make_lifecycle(status=JobStatus.PENDING)
        self.fake_mlflow.add_job("job-1", lifecycle, self._make_payload())

        poll_once()

        submitted = self.fake_ray.submitted[0]
        working_dir = submitted["runtime_env"]["working_dir"]
        self.assertTrue(Path(working_dir).is_dir())
        self.assertTrue((Path(working_dir) / "pyproject.toml").exists())

    def test_submitted_job_uses_requirements_txt(self) -> None:
        lifecycle = self._make_lifecycle(status=JobStatus.PENDING)
        self.fake_mlflow.add_job("job-1", lifecycle, self._make_payload())

        poll_once()

        submitted = self.fake_ray.submitted[0]
        self.assertTrue(submitted["runtime_env"]["pip"].endswith("/requirements.txt"))


if __name__ == "__main__":
    unittest.main()
