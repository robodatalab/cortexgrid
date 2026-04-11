from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cortexflow.config import CortexConfig, set_config
from cortexflow.mlflow_util import (
    log_metric,
    log_metrics,
    log_params,
    log_artifact,
    try_create_experiment_and_run,
    get_experiment_list,
)


RUN_ID = "test-run-123"


class TestMlflowUtil(unittest.TestCase):
    def setUp(self) -> None:
        set_config(CortexConfig(
            mlflow_tracking_uri="http://test:5000",
            run_id=RUN_ID,
        ))
        self.client = MagicMock()
        self.patcher = patch(
            "cortexflow.mlflow_util.get_mlflow_client",
            return_value=self.client,
        )
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        set_config(None)  # type: ignore[arg-type]

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

    def test_get_experiment_list_maps_names_to_run_ids(self) -> None:
        exp_a = MagicMock(experiment_id="1", name="alpha")
        exp_a.name = "alpha"
        exp_b = MagicMock(experiment_id="2", name="beta")
        exp_b.name = "beta"
        self.client.search_experiments.return_value = [exp_a, exp_b]

        run_a1 = MagicMock()
        run_a1.info.run_id = "run-a1"
        run_b1 = MagicMock()
        run_b1.info.run_id = "run-b1"
        run_b2 = MagicMock()
        run_b2.info.run_id = "run-b2"
        self.client.search_runs.side_effect = [[run_a1], [run_b1, run_b2]]

        result = get_experiment_list()

        self.assertEqual(result, {"alpha": ["run-a1"], "beta": ["run-b1", "run-b2"]})


if __name__ == "__main__":
    unittest.main()
