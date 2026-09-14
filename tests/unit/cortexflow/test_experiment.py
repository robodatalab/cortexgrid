from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cortexgrid.experiment import (
    Experiment,
    get_experiment_by_run_name,
    set_instance,
)


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

    def test_get_experiment_by_run_name_returns_matching_experiment(self) -> None:
        self.fake_mlflow.search_experiments.return_value = [
            MagicMock(experiment_id="e1"),
            MagicMock(experiment_id="e2"),
        ]
        self.fake_mlflow.search_runs.return_value = [
            MagicMock(info=MagicMock(experiment_id="e2", run_id="r-7")),
        ]
        self.fake_mlflow.get_experiment.return_value = MagicMock(name=None)
        self.fake_mlflow.get_experiment.return_value.name = "trainers"

        result = get_experiment_by_run_name("boogey-46")

        self.fake_mlflow.search_runs.assert_called_once_with(
            experiment_ids=["e1", "e2"],
            filter_string="attributes.run_name = 'boogey-46'",
            max_results=1,
        )
        self.fake_mlflow.get_experiment.assert_called_once_with("e2")
        self.assertEqual(result, Experiment("trainers", "r-7"))

    def test_get_experiment_by_run_name_raises_when_no_runs_match(self) -> None:
        self.fake_mlflow.search_experiments.return_value = [
            MagicMock(experiment_id="e1"),
        ]
        self.fake_mlflow.search_runs.return_value = []
        with self.assertRaises(ValueError):
            get_experiment_by_run_name("missing")

    def test_get_experiment_by_run_name_raises_when_no_experiments_exist(self) -> None:
        self.fake_mlflow.search_experiments.return_value = []
        with self.assertRaises(ValueError):
            get_experiment_by_run_name("anything")

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



if __name__ == "__main__":
    unittest.main()
