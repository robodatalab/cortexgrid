from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from cortexflow.config import CortexConfig, set_config
from cortexflow.mlflow_util import mlflow_run, log_metric, log_metrics, log_params, log_artifact, try_create_experiment_and_run


def _mock_mlflow() -> MagicMock:
    """Create a mock that stands in for the mlflow module."""
    mock = MagicMock()
    mock.ActiveRun = MagicMock
    return mock


class TestMlflowRun(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(mlflow_tracking_uri="http://test:5000"))
        self.mock_mlflow = _mock_mlflow()
        mock_run = MagicMock()
        self.mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=mock_run)
        self.mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)
        self.mock_run = mock_run
        self.modules_patcher = patch.dict(sys.modules, {
            "mlflow": self.mock_mlflow,
            "mlflow.tracking": self.mock_mlflow.tracking,
            "mlflow.artifacts": self.mock_mlflow.artifacts,
        })
        self.modules_patcher.start()
        if "cortexflow.mlflow_util" in sys.modules:
            del sys.modules["cortexflow.mlflow_util"]

    def tearDown(self) -> None:
        self.modules_patcher.stop()
        set_config(None)  # type: ignore[arg-type]
        if "cortexflow.mlflow_util" in sys.modules:
            del sys.modules["cortexflow.mlflow_util"]

    def test_sets_experiment_and_starts_run(self) -> None:
        with mlflow_run("test-experiment", run_name="v1") as run:
            self.assertIs(run, self.mock_run)

        self.mock_mlflow.set_experiment.assert_called_once_with("test-experiment")
        self.mock_mlflow.start_run.assert_called_once_with(
            run_id=None, run_name="v1", tags={}
        )


class TestMlflowRunAutoResume(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(mlflow_tracking_uri="http://test:5000"))
        self.mock_mlflow = _mock_mlflow()
        mock_run = MagicMock()
        self.mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=mock_run)
        self.mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)
        self.mock_run = mock_run
        self.modules_patcher = patch.dict(sys.modules, {
            "mlflow": self.mock_mlflow,
            "mlflow.tracking": self.mock_mlflow.tracking,
            "mlflow.artifacts": self.mock_mlflow.artifacts,
        })
        self.modules_patcher.start()
        if "cortexflow.mlflow_util" in sys.modules:
            del sys.modules["cortexflow.mlflow_util"]

    def tearDown(self) -> None:
        self.modules_patcher.stop()
        set_config(None)  # type: ignore[arg-type]
        if "cortexflow.mlflow_util" in sys.modules:
            del sys.modules["cortexflow.mlflow_util"]

    def test_resumes_existing_run_on_retry(self) -> None:
        mock_client = self.mock_mlflow.tracking.MlflowClient.return_value
        mock_exp = MagicMock()
        mock_exp.experiment_id = "exp_1"
        mock_client.get_experiment_by_name.return_value = mock_exp

        mock_prev_run = MagicMock()
        mock_prev_run.info.run_id = "run_prev_123"
        mock_client.search_runs.return_value = [mock_prev_run]

        with patch.dict(os.environ, {"CORTEXFLOW_JOB_ID": "job-uuid-1"}):
            with mlflow_run("test-exp", run_name="v1"):
                pass

        self.mock_mlflow.start_run.assert_called_once_with(
            run_id="run_prev_123",
            run_name="v1",
            tags={"cortexflow.job_id": "job-uuid-1"},
        )

    def test_creates_new_run_when_no_existing(self) -> None:
        mock_client = self.mock_mlflow.tracking.MlflowClient.return_value
        mock_client.get_experiment_by_name.return_value = None

        with patch.dict(os.environ, {"CORTEXFLOW_JOB_ID": "job-uuid-2"}):
            with mlflow_run("test-exp", run_name="v1"):
                pass

        self.mock_mlflow.start_run.assert_called_once_with(
            run_id=None,
            run_name="v1",
            tags={"cortexflow.job_id": "job-uuid-2"},
        )

    def test_no_job_id_behaves_normally(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with mlflow_run("test-exp", run_name="v1"):
                pass

        self.mock_mlflow.start_run.assert_called_once_with(
            run_id=None,
            run_name="v1",
            tags={},
        )


class TestLogFunctions(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig())
        self.mock_mlflow = _mock_mlflow()
        self.modules_patcher = patch.dict(sys.modules, {
            "mlflow": self.mock_mlflow,
            "mlflow.tracking": self.mock_mlflow.tracking,
            "mlflow.artifacts": self.mock_mlflow.artifacts,
        })
        self.modules_patcher.start()
        if "cortexflow.mlflow_util" in sys.modules:
            del sys.modules["cortexflow.mlflow_util"]

    def tearDown(self) -> None:
        self.modules_patcher.stop()
        set_config(None)  # type: ignore[arg-type]
        if "cortexflow.mlflow_util" in sys.modules:
            del sys.modules["cortexflow.mlflow_util"]

    def test_log_metric(self) -> None:
        log_metric("loss", 0.5, step=3)
        self.mock_mlflow.log_metric.assert_called_once_with("loss", 0.5, step=3)

    def test_log_metrics(self) -> None:
        log_metrics({"loss": 0.5, "acc": 0.9}, step=1)
        self.mock_mlflow.log_metrics.assert_called_once_with(
            {"loss": 0.5, "acc": 0.9}, step=1
        )

    def test_log_params(self) -> None:
        log_params({"lr": 0.001, "epochs": 10})
        self.mock_mlflow.log_params.assert_called_once_with({"lr": 0.001, "epochs": 10})

    def test_log_artifact(self) -> None:
        log_artifact("/tmp/model.pt", artifact_path="models")
        self.mock_mlflow.log_artifact.assert_called_once_with(
            "/tmp/model.pt", artifact_path="models"
        )


class TestTryCreateExperimentAndRun(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.patcher = patch(
            "cortexflow.mlflow_util.get_mlflow_client",
            return_value=self.client,
        )
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_creates_experiment_when_none_exists(self) -> None:
        self.client.get_experiment_by_name.return_value = None
        self.client.create_experiment.return_value = "exp-42"

        try_create_experiment_and_run("my-experiment")

        self.client.get_experiment_by_name.assert_called_once_with(name="my-experiment")
        self.client.create_experiment.assert_called_once_with(name="my-experiment")
        self.client.create_run.assert_called_once()
        kwargs = self.client.create_run.call_args.kwargs
        self.assertEqual(kwargs["experiment_id"], "exp-42")
        self.assertIsInstance(kwargs["run_name"], str)
        self.assertTrue(kwargs["run_name"])

    def test_creates_run_in_existing_experiment(self) -> None:
        existing = MagicMock()
        existing.experiment_id = "exp-7"
        self.client.get_experiment_by_name.return_value = existing

        try_create_experiment_and_run("my-experiment")

        self.client.get_experiment_by_name.assert_called_once_with(name="my-experiment")
        self.client.create_experiment.assert_not_called()
        self.client.create_run.assert_called_once()
        kwargs = self.client.create_run.call_args.kwargs
        self.assertEqual(kwargs["experiment_id"], "exp-7")
        self.assertIsInstance(kwargs["run_name"], str)
        self.assertTrue(kwargs["run_name"])


if __name__ == "__main__":
    unittest.main()
