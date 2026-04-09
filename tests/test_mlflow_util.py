from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from cortexflow.config import CortexConfig, set_config


def _mock_mlflow() -> MagicMock:
    """Create a mock that stands in for the mlflow module."""
    mock = MagicMock()
    mock.ActiveRun = MagicMock
    return mock


class TestEnsureConfigured(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patcher = patch.dict(os.environ, {}, clear=True)
        self.env_patcher.start()
        set_config(CortexConfig(
            mlflow_tracking_uri="http://test:5000",
            mlflow_s3_endpoint_url="http://test:9000",
            s3_access_key="key",
            s3_secret_key="secret",
        ))
        self.mock_mlflow = _mock_mlflow()
        self.modules_patcher = patch.dict(sys.modules, {
            "mlflow": self.mock_mlflow,
            "mlflow.tracking": self.mock_mlflow.tracking,
            "mlflow.artifacts": self.mock_mlflow.artifacts,
        })
        self.modules_patcher.start()
        # Force reimport so the module uses our mock
        if "cortexflow.mlflow_util" in sys.modules:
            del sys.modules["cortexflow.mlflow_util"]

    def tearDown(self) -> None:
        self.modules_patcher.stop()
        self.env_patcher.stop()
        set_config(None)  # type: ignore[arg-type]
        if "cortexflow.mlflow_util" in sys.modules:
            del sys.modules["cortexflow.mlflow_util"]

    def test_sets_tracking_uri(self) -> None:
        from cortexflow.mlflow_util import _ensure_configured
        _ensure_configured()
        self.mock_mlflow.set_tracking_uri.assert_called_once_with("http://test:5000")

    def test_sets_s3_env_vars(self) -> None:
        from cortexflow.mlflow_util import _ensure_configured
        _ensure_configured()
        self.assertEqual(os.environ["MLFLOW_S3_ENDPOINT_URL"], "http://test:9000")
        self.assertEqual(os.environ["AWS_ACCESS_KEY_ID"], "key")
        self.assertEqual(os.environ["AWS_SECRET_ACCESS_KEY"], "secret")

    def test_does_not_override_existing_env_vars(self) -> None:
        os.environ["AWS_ACCESS_KEY_ID"] = "existing"
        from cortexflow.mlflow_util import _ensure_configured
        _ensure_configured()
        self.assertEqual(os.environ["AWS_ACCESS_KEY_ID"], "existing")


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
        from cortexflow.mlflow_util import mlflow_run
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
            from cortexflow.mlflow_util import mlflow_run
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
            from cortexflow.mlflow_util import mlflow_run
            with mlflow_run("test-exp", run_name="v1"):
                pass

        self.mock_mlflow.start_run.assert_called_once_with(
            run_id=None,
            run_name="v1",
            tags={"cortexflow.job_id": "job-uuid-2"},
        )

    def test_no_job_id_behaves_normally(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            from cortexflow.mlflow_util import mlflow_run
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
        from cortexflow.mlflow_util import log_metric
        log_metric("loss", 0.5, step=3)
        self.mock_mlflow.log_metric.assert_called_once_with("loss", 0.5, step=3)

    def test_log_metrics(self) -> None:
        from cortexflow.mlflow_util import log_metrics
        log_metrics({"loss": 0.5, "acc": 0.9}, step=1)
        self.mock_mlflow.log_metrics.assert_called_once_with(
            {"loss": 0.5, "acc": 0.9}, step=1
        )

    def test_log_params(self) -> None:
        from cortexflow.mlflow_util import log_params
        log_params({"lr": 0.001, "epochs": 10})
        self.mock_mlflow.log_params.assert_called_once_with({"lr": 0.001, "epochs": 10})

    def test_log_artifact(self) -> None:
        from cortexflow.mlflow_util import log_artifact
        log_artifact("/tmp/model.pt", artifact_path="models")
        self.mock_mlflow.log_artifact.assert_called_once_with(
            "/tmp/model.pt", artifact_path="models"
        )


if __name__ == "__main__":
    unittest.main()
