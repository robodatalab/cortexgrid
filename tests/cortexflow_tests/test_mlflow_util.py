from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cortexflow.experiment import Experiment, clear_instance, list_experiments, set_instance
from cortexflow.mlflow_util import (
    log_metric,
    log_metrics,
    log_params,
    log_artifact,
    list_run_metrics,
    get_metric_history,
    list_run_params,
    list_run_artifacts,
)


RUN_ID = "test-run-123"


class TestMlflowUtil(unittest.TestCase):
    def setUp(self) -> None:
        set_instance(
            Experiment(
                experiment_name="experiment",
                run_id=RUN_ID,
            )
        )
        self.client = MagicMock()
        self.patcher = patch(
            "cortexflow.mlflow_util.get_mlflow_client",
            return_value=self.client,
        )
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        clear_instance()

    def test_log_metric_hits_configured_run(self) -> None:
        log_metric("loss", 0.5, step=3)
        self.client.log_metric.assert_called_once_with(RUN_ID, "loss", 0.5, step=3)

    def test_log_metrics_batches_under_configured_run(self) -> None:
        log_metrics({"loss": 0.5, "acc": 0.9}, step=1)

        self.client.log_batch.assert_called_once()
        args, kwargs = self.client.log_batch.call_args
        self.assertEqual(args[0], RUN_ID)
        by_key = {m.key: m for m in kwargs["metrics"]}
        self.assertEqual(by_key["loss"].value, 0.5)
        self.assertEqual(by_key["loss"].step, 1)
        self.assertEqual(by_key["acc"].value, 0.9)
        self.assertEqual(by_key["acc"].step, 1)

    def test_log_params_hits_configured_run(self) -> None:
        log_params({"lr": 0.001, "epochs": 10})
        self.client.log_param.assert_any_call(RUN_ID, "lr", 0.001)
        self.client.log_param.assert_any_call(RUN_ID, "epochs", 10)
        self.assertEqual(self.client.log_param.call_count, 2)

    def test_log_artifact_hits_configured_run(self) -> None:
        log_artifact("/tmp/model.pt", artifact_path="models")
        self.client.log_artifact.assert_called_once_with(
            RUN_ID, "/tmp/model.pt", artifact_path="models"
        )

    def test_list_run_metrics_returns_keys(self) -> None:
        self.client.get_run.return_value = MagicMock(data=MagicMock(metrics={"loss": 0.5, "acc": 0.9}))
        result = list_run_metrics(RUN_ID)
        self.assertEqual(sorted(result), ["acc", "loss"])

    def test_get_metric_history_returns_points(self) -> None:
        m1 = MagicMock(step=1, value=0.9, timestamp=1000)
        m2 = MagicMock(step=2, value=0.8, timestamp=2000)
        self.client.get_metric_history.return_value = [m1, m2]
        result = get_metric_history(RUN_ID, "loss")
        self.assertEqual(result, [
            {"step": 1, "value": 0.9, "timestamp": 1000},
            {"step": 2, "value": 0.8, "timestamp": 2000},
        ])

    def test_list_run_params_returns_dict(self) -> None:
        self.client.get_run.return_value = MagicMock(data=MagicMock(params={"lr": "0.001", "epochs": "10"}))
        result = list_run_params(RUN_ID)
        self.assertEqual(result, {"lr": "0.001", "epochs": "10"})

    def test_list_run_artifacts_returns_paths(self) -> None:
        a1 = MagicMock(path="model.pt")
        a2 = MagicMock(path="job/job-1")
        self.client.list_artifacts.return_value = [a1, a2]
        result = list_run_artifacts(RUN_ID)
        self.assertEqual(result, ["model.pt", "job/job-1"])

    @patch("cortexflow.experiment.MlflowClient")
    @patch("cortexflow.experiment.get_mlflow_tracking_uri", return_value="http://test:5000")
    def test_list_experiments_returns_experiment_objects(self, _mock_uri: MagicMock, mock_mlflow_cls: MagicMock) -> None:
        fake_client = MagicMock()
        mock_mlflow_cls.return_value = fake_client

        exp_a = MagicMock(experiment_id="1")
        exp_a.name = "alpha"
        exp_b = MagicMock(experiment_id="2")
        exp_b.name = "beta"
        fake_client.search_experiments.return_value = [exp_a, exp_b]

        run_a1 = MagicMock()
        run_a1.info.run_id = "run-a1"
        run_b1 = MagicMock()
        run_b1.info.run_id = "run-b1"
        run_b2 = MagicMock()
        run_b2.info.run_id = "run-b2"
        fake_client.search_runs.side_effect = [[run_a1], [run_b1, run_b2]]

        result = list_experiments()

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], Experiment("alpha", "run-a1"))
        self.assertEqual(result[1], Experiment("beta", "run-b1"))
        self.assertEqual(result[2], Experiment("beta", "run-b2"))


if __name__ == "__main__":
    unittest.main()
