from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cortexflow.experiment import Experiment, get_mlflow_run_url, set_instance


class TestExperiment(unittest.TestCase):
    def setUp(self) -> None:
        set_instance(None)
        self.fake_mlflow = MagicMock()
        for p in (
            patch("boto3.client"),
            patch(
                "cortexflow.experiment.MlflowClient",
                return_value=self.fake_mlflow,
            ),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(set_instance, None)

    def test_get_instance_raises_before_init(self) -> None:
        with self.assertRaises(ValueError):
            Experiment.get_instance()

    def test_init_creates_experiment_and_run(self) -> None:
        exp = Experiment.init()

        self.assertTrue(exp.experiment_name)
        self.assertTrue(exp.run_id)
        self.assertIs(Experiment.get_instance(), exp)

    def test_init_returns_existing_instance_on_second_call(self) -> None:
        first = Experiment.init()
        second = Experiment.init()
        self.assertIs(first, second)

    def test_init_raises_when_called_with_different_name(self) -> None:
        Experiment.init("my-exp")
        with self.assertRaises(ValueError):
            Experiment.init("other-exp")

    def test_from_experiment_binds_to_existing_run(self) -> None:
        exp = Experiment.from_experiment("my-exp", "run-xyz")

        self.assertEqual(exp.experiment_name, "my-exp")
        self.assertEqual(exp.run_id, "run-xyz")
        self.assertIs(Experiment.get_instance(), exp)

    def test_from_experiment_raises_when_singleton_mismatches(self) -> None:
        Experiment.from_experiment("my-exp", "run-xyz")
        with self.assertRaises(ValueError):
            Experiment.from_experiment("other-exp", "other-run")

    def test_get_jobs_lists_cortexflow_job_ids(self) -> None:
        self.fake_mlflow.list_artifacts.return_value = [
            MagicMock(path="job/job-1", is_dir=True),
            MagicMock(path="job/job-2", is_dir=True),
        ]

        exp = Experiment.from_experiment("my-exp", "run-xyz")
        result = exp.get_jobs()

        self.fake_mlflow.list_artifacts.assert_called_once_with(
            "run-xyz", path="job"
        )
        self.assertEqual(result, ["job-1", "job-2"])

    @patch("cortexflow.experiment.get_secret", return_value="100.80.27.32")
    def test_get_mlflow_run_url_builds_url(self, _mock: MagicMock) -> None:
        run = MagicMock()
        run.info.experiment_id = "7"
        self.fake_mlflow.get_run.return_value = run

        url = get_mlflow_run_url("run-xyz")

        self.assertEqual(url, "http://100.80.27.32:5000/#/experiments/7/runs/run-xyz")


if __name__ == "__main__":
    unittest.main()
