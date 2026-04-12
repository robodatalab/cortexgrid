from __future__ import annotations

import json
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


class CapturingMLflow:
    """Fake MlflowClient that captures artifact file contents."""

    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}

    def log_artifact(self, run_id: str, local_path: str, artifact_path: str = "") -> None:
        key = f"{artifact_path}/{Path(local_path).name}"
        self.artifacts[key] = Path(local_path).read_bytes()

    def download_artifacts(self, run_id: str, path: str) -> str:
        return ""


class TestRemote(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()
        self.fake_mlflow = CapturingMLflow()
        patchers = [
            patch("boto3.client"),
            patch("cortexflow.experiment.MlflowClient"),
            patch(
                "cortexflow.jobs.MlflowClient",
                return_value=self.fake_mlflow,
            ),
            patch(
                "cortexflow.jobs.subprocess.run",
                return_value=MagicMock(stdout="numpy==1.26\ntorch==2.5\n"),
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

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

        self.assertIn(f"job/{job_id}/payload.pkl", self.fake_mlflow.artifacts)
        self.assertIn(f"job/{job_id}/lifecycle.json", self.fake_mlflow.artifacts)

    def test_initial_lifecycle_is_pending(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        raw = self.fake_mlflow.artifacts[f"job/{job_id}/lifecycle.json"]
        lifecycle = JobLifecycle.from_json(raw.decode())
        self.assertEqual(lifecycle.status, JobStatus.PENDING)
        self.assertIsNone(lifecycle.error)
        self.assertFalse(lifecycle.retry)

    def test_lifecycle_includes_retry_flag(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None, retry=True)

        raw = self.fake_mlflow.artifacts[f"job/{job_id}/lifecycle.json"]
        lifecycle = JobLifecycle.from_json(raw.decode())
        self.assertTrue(lifecycle.retry)

    def test_uploaded_payload_round_trips_through_cloudpickle(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        raw = self.fake_mlflow.artifacts[f"job/{job_id}/payload.pkl"]
        payload: Payload = cloudpickle.loads(raw)
        self.assertIsNotNone(payload.fn)
        self.assertEqual(payload.experiment.experiment_name, "exp")

    def test_payload_includes_resource_requests(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None, num_gpus=2, num_cpus=4)

        raw = self.fake_mlflow.artifacts[f"job/{job_id}/payload.pkl"]
        payload: Payload = cloudpickle.loads(raw)
        self.assertEqual(payload.num_gpus, 2)
        self.assertEqual(payload.num_cpus, 4)

    def test_payload_includes_pip_requirements(self) -> None:
        set_instance(_make_experiment())

        job_id = cortexflow.remote(lambda: None)

        raw = self.fake_mlflow.artifacts[f"job/{job_id}/payload.pkl"]
        payload: Payload = cloudpickle.loads(raw)
        self.assertIn("numpy==1.26", payload.pip_requirements)
        self.assertIn("torch==2.5", payload.pip_requirements)

    def test_get_job_status_returns_lifecycle(self) -> None:
        import tempfile

        exp = _make_experiment()
        set_instance(exp)
        lifecycle = JobLifecycle(experiment=exp, job_id="job-123", status=JobStatus.RUNNING, retry=True)
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "lifecycle.json").write_text(lifecycle.to_json())
        self.fake_mlflow.download_artifacts = lambda run_id, path: str(tmpdir / "lifecycle.json")

        result = cortexflow.get_job_status(exp, "job-123")

        self.assertIsInstance(result, JobLifecycle)
        self.assertEqual(result.status, JobStatus.RUNNING)
        self.assertTrue(result.retry)

    def test_get_job_status_returns_error_on_failure(self) -> None:
        import tempfile

        exp = _make_experiment()
        set_instance(exp)
        lifecycle = JobLifecycle(experiment=exp, job_id="job-456", status=JobStatus.FAILED, error="OOM killed")
        tmpdir = Path(tempfile.mkdtemp())
        (tmpdir / "lifecycle.json").write_text(lifecycle.to_json())
        self.fake_mlflow.download_artifacts = lambda run_id, path: str(tmpdir / "lifecycle.json")

        result = cortexflow.get_job_status(exp, "job-456")

        self.assertEqual(result.status, JobStatus.FAILED)
        self.assertEqual(result.error, "OOM killed")


if __name__ == "__main__":
    unittest.main()
