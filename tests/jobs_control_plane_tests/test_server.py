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

    def log_artifact(self, run_id: str, local_path: str, artifact_path: str = "") -> None:
        dest_dir = self.artifact_root / artifact_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(local_path).name
        shutil.copy2(local_path, dest)

    def add_job(self, experiment: Experiment, job_id: str, lifecycle: JobLifecycle, payload_bytes: bytes = b"") -> None:
        job_dir = self.artifact_root / "job" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "lifecycle.json").write_text(lifecycle.to_json())
        if payload_bytes:
            (job_dir / "payload.pkl").write_bytes(payload_bytes)


class FakeRay:
    """Fake JobSubmissionClient."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.statuses: dict[str, str] = {}
        self.logs_by_id: dict[str, str] = {}

    def submit_job(self, entrypoint: str, runtime_env: dict, **kwargs: Any) -> str:
        ray_job_id = f"ray_{len(self.submitted)}"
        self.submitted.append({"entrypoint": entrypoint, "runtime_env": runtime_env, **kwargs})
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
            patch("jobs_control_plane.server.list_experiments", return_value=[self.exp]),
            patch("jobs_control_plane.server.JobSubmissionClient", return_value=self.fake_ray),
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(os.chdir, self.original_cwd)
        self.addCleanup(shutil.rmtree, self.project_root, True)

    def _make_payload_bytes(self) -> bytes:
        return cloudpickle.dumps(
            Payload(job_id="job-1", fn=lambda: None, args=(), kwargs={}, experiment=self.exp, pip_requirements="")
        )

    def test_pending_job_gets_submitted_to_ray(self) -> None:
        lifecycle = JobLifecycle(experiment=self.exp, job_id="job-1", status=JobStatus.PENDING)
        self.fake_mlflow.add_job(self.exp, "job-1", lifecycle, self._make_payload_bytes())

        poll_once()

        self.assertEqual(len(self.fake_ray.submitted), 1)
        updated = JobLifecycle.from_json(
            (self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json").read_text()
        )
        self.assertEqual(updated.status, JobStatus.RUNNING)
        self.assertIsNotNone(updated.ray_job_id)

    def test_running_job_transitions_to_finished_on_success(self) -> None:
        lifecycle = JobLifecycle(experiment=self.exp, job_id="job-1", status=JobStatus.RUNNING, ray_job_id="ray_0")
        self.fake_mlflow.add_job(self.exp, "job-1", lifecycle)
        self.fake_ray.statuses["ray_0"] = "SUCCEEDED"

        poll_once()

        updated = JobLifecycle.from_json(
            (self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json").read_text()
        )
        self.assertEqual(updated.status, JobStatus.FINISHED)

    def test_running_job_transitions_to_failed_on_failure(self) -> None:
        lifecycle = JobLifecycle(experiment=self.exp, job_id="job-1", status=JobStatus.RUNNING, ray_job_id="ray_0")
        self.fake_mlflow.add_job(self.exp, "job-1", lifecycle)
        self.fake_ray.statuses["ray_0"] = "FAILED"
        self.fake_ray.logs_by_id["ray_0"] = "CUDA OOM"

        poll_once()

        updated = JobLifecycle.from_json(
            (self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json").read_text()
        )
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertIn("CUDA OOM", updated.error or "")

    def test_failed_job_with_retry_gets_resubmitted(self) -> None:
        lifecycle = JobLifecycle(experiment=self.exp, job_id="job-1", status=JobStatus.FAILED, retry=True, error="OOM")
        self.fake_mlflow.add_job(self.exp, "job-1", lifecycle, self._make_payload_bytes())

        poll_once()

        self.assertEqual(len(self.fake_ray.submitted), 1)
        updated = JobLifecycle.from_json(
            (self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json").read_text()
        )
        self.assertEqual(updated.status, JobStatus.RUNNING)

    def test_failed_job_without_retry_stays_failed(self) -> None:
        lifecycle = JobLifecycle(experiment=self.exp, job_id="job-1", status=JobStatus.FAILED, retry=False, error="OOM")
        self.fake_mlflow.add_job(self.exp, "job-1", lifecycle)

        poll_once()

        self.assertEqual(len(self.fake_ray.submitted), 0)
        updated = JobLifecycle.from_json(
            (self.fake_mlflow.artifact_root / "job" / "job-1" / "lifecycle.json").read_text()
        )
        self.assertEqual(updated.status, JobStatus.FAILED)

    def test_finished_job_is_not_touched(self) -> None:
        lifecycle = JobLifecycle(experiment=self.exp, job_id="job-1", status=JobStatus.FINISHED, ray_job_id="ray_0")
        self.fake_mlflow.add_job(self.exp, "job-1", lifecycle)

        poll_once()

        self.assertEqual(len(self.fake_ray.submitted), 0)


if __name__ == "__main__":
    unittest.main()
