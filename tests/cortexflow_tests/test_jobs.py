from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cloudpickle  # type: ignore

import cortexflow
from cortexflow.experiment import Experiment, clear_instance, set_instance
from cortexflow.jobs import JobStatus, Payload


def _make_experiment(experiment_name: str = "exp", run_id: str = "run") -> Experiment:
    return Experiment(
        experiment_name=experiment_name,
        run_id=run_id,
        ray_address="http://test:8265",
        dgx_ip="",
        mlflow_tracking_uri="http://test:5000",
        mlflow_s3_endpoint_url="",
        s3_endpoint_url="",
        s3_access_key="",
        s3_secret_key="",
        s3_default_bucket="",
        github_token="",
    )


def _get_artifact_call(fake_mlflow: MagicMock, filename: str) -> tuple[tuple, dict]:
    """Find the log_artifact call that uploaded a file with the given name."""
    for call in fake_mlflow.log_artifact.call_args_list:
        args, kwargs = call
        if Path(args[1]).name == filename:
            return args, kwargs
    raise AssertionError(f"No log_artifact call with filename {filename}")


class TestRemote(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()
        self.fake_mlflow = MagicMock()
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

    def test_remote_uploads_payload_and_status(self) -> None:
        set_instance(_make_experiment(run_id="run-xyz"))

        job_id = cortexflow.remote(lambda: None)

        self.assertEqual(self.fake_mlflow.log_artifact.call_count, 2)
        payload_args, payload_kwargs = _get_artifact_call(self.fake_mlflow, "payload.pkl")
        self.assertEqual(payload_args[0], "run-xyz")
        self.assertEqual(payload_kwargs["artifact_path"], f"job/{job_id}")

        status_args, status_kwargs = _get_artifact_call(self.fake_mlflow, "status.json")
        self.assertEqual(status_args[0], "run-xyz")
        self.assertEqual(status_kwargs["artifact_path"], f"job/{job_id}")

    def test_initial_status_is_pending(self) -> None:
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None)

        status_args, _ = _get_artifact_call(self.fake_mlflow, "status.json")
        data = json.loads(Path(status_args[1]).read_text())
        self.assertEqual(data["status"], "pending")
        self.assertIsNone(data["error"])

    def test_uploaded_payload_round_trips_through_cloudpickle(self) -> None:
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None)

        args, _ = _get_artifact_call(self.fake_mlflow, "payload.pkl")
        payload: Payload = cloudpickle.loads(Path(args[1]).read_bytes())
        self.assertIsNotNone(payload.fn)
        self.assertEqual(payload.experiment.experiment_name, "exp")

    def test_payload_includes_resource_requests(self) -> None:
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None, num_gpus=2, num_cpus=4)

        args, _ = _get_artifact_call(self.fake_mlflow, "payload.pkl")
        payload: Payload = cloudpickle.loads(Path(args[1]).read_bytes())
        self.assertEqual(payload.num_gpus, 2)
        self.assertEqual(payload.num_cpus, 4)

    def test_payload_includes_retry_flag(self) -> None:
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None, retry=True)

        args, _ = _get_artifact_call(self.fake_mlflow, "payload.pkl")
        payload: Payload = cloudpickle.loads(Path(args[1]).read_bytes())
        self.assertTrue(payload.retry)

    def test_payload_includes_pip_requirements(self) -> None:
        set_instance(_make_experiment())

        cortexflow.remote(lambda: None)

        args, _ = _get_artifact_call(self.fake_mlflow, "payload.pkl")
        payload: Payload = cloudpickle.loads(Path(args[1]).read_bytes())
        self.assertIn("numpy==1.26", payload.pip_requirements)
        self.assertIn("torch==2.5", payload.pip_requirements)

    def test_get_job_status_reads_status_json(self) -> None:
        exp = _make_experiment()
        set_instance(exp)
        status_data = json.dumps({"status": "running", "error": None})
        self.fake_mlflow.download_artifacts.return_value = str(
            self._write_temp("status.json", status_data)
        )

        status, error = cortexflow.get_job_status(exp, "job-123")

        self.assertEqual(status, JobStatus.RUNNING)
        self.assertIsNone(error)

    def test_get_job_status_returns_error_on_failure(self) -> None:
        exp = _make_experiment()
        set_instance(exp)
        status_data = json.dumps({"status": "failed", "error": "OOM killed"})
        self.fake_mlflow.download_artifacts.return_value = str(
            self._write_temp("status.json", status_data)
        )

        status, error = cortexflow.get_job_status(exp, "job-456")

        self.assertEqual(status, JobStatus.FAILED)
        self.assertEqual(error, "OOM killed")

    def _write_temp(self, name: str, content: str) -> Path:
        import tempfile

        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / name
        path.write_text(content)
        return path


if __name__ == "__main__":
    unittest.main()
