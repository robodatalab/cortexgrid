from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mlflow.tracking import MlflowClient

from cortexgrid.experiment import (
    DELETE_REQUESTED_TAG,
    Experiment,
    delete_experiment,
    get_experiment_by_run_name,
    set_instance,
)


class TestExperiment(unittest.TestCase):
    def setUp(self) -> None:
        set_instance(None)
        self.fake_mlflow = MagicMock()
        for p in (
            patch("cortexgrid.infra.get_secret", return_value="http://mlflow"),
            patch(
                "cortexgrid.experiment.MlflowClient",
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
        )
        self.fake_mlflow.get_experiment.assert_called_once_with("e2")
        self.assertEqual(result, Experiment("trainers", "r-7"))

    def test_get_experiment_by_run_name_skips_a_run_on_its_way_out(self) -> None:
        """A deleted run must not answer to its name while it is torn down."""
        self.fake_mlflow.search_experiments.return_value = [
            MagicMock(experiment_id="e1")
        ]
        self.fake_mlflow.search_runs.return_value = [
            MagicMock(
                info=MagicMock(experiment_id="e1", run_id="r-7"),
                data=MagicMock(tags={DELETE_REQUESTED_TAG: "true"}),
            ),
        ]

        with self.assertRaises(ValueError):
            get_experiment_by_run_name("boogey-46")

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

    def test_get_jobs_lists_cortexgrid_job_ids(self) -> None:
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


class TestDeletedExperimentNames(unittest.TestCase):
    """Against a real MLflow store (SQLite), because its rules on deleted
    experiments are what these paths work around: a deleted experiment keeps
    its name reserved, cannot be renamed, and cannot take new runs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tracking_uri = f"sqlite:///{Path(cls._tmp.name) / 'mlflow.db'}"
        cls.client = MlflowClient(tracking_uri=cls.tracking_uri)
        cls.client.search_experiments()  # create the schema once for the class

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        set_instance(None)
        self.addCleanup(set_instance, None)
        p = patch(
            "cortexgrid.experiment.get_mlflow_tracking_uri",
            return_value=self.tracking_uri,
        )
        p.start()
        self.addCleanup(p.stop)

    def _run_experiment_id(self, exp: Experiment) -> str:
        return self.client.get_run(exp.run_id).info.experiment_id

    def test_init_adds_a_run_to_the_active_experiment_of_that_name(self) -> None:
        experiment_id = self.client.create_experiment("reused")

        exp = Experiment.init("reused")

        self.assertEqual(self._run_experiment_id(exp), experiment_id)

    def test_init_creates_a_new_experiment_when_the_named_one_was_deleted(
        self,
    ) -> None:
        # Deleted outside cortexgrid (e.g. the MLflow UI), so still under its name.
        old_id = self.client.create_experiment("reborn")
        self.client.delete_experiment(old_id)

        exp = Experiment.init("reborn")

        new = self.client.get_experiment_by_name("reborn")
        assert new is not None
        self.assertNotEqual(new.experiment_id, old_id)
        self.assertEqual(new.lifecycle_stage, "active")
        self.assertEqual(self._run_experiment_id(exp), new.experiment_id)

    def test_init_leaves_the_replaced_experiment_deleted_under_another_name(
        self,
    ) -> None:
        old_id = self.client.create_experiment("replaced")
        self.client.delete_experiment(old_id)

        Experiment.init("replaced")

        old = self.client.get_experiment(old_id)
        self.assertEqual(old.lifecycle_stage, "deleted")
        self.assertEqual(old.name, f"replaced__deleted__{old_id}")

    def test_delete_experiment_releases_the_name(self) -> None:
        """The name is free on return; the record waits for the control plane."""
        old_id = self.client.create_experiment("released")

        delete_experiment("released")

        self.assertIsNone(self.client.get_experiment_by_name("released"))
        old = self.client.get_experiment(old_id)
        self.assertEqual(old.name, f"released__deleted__{old_id}")
        self.assertEqual(old.lifecycle_stage, "active")
        self.assertEqual(old.tags.get(DELETE_REQUESTED_TAG), "true")

    def test_init_after_delete_experiment_creates_a_new_experiment(self) -> None:
        old_id = self.client.create_experiment("cycled")
        delete_experiment("cycled")

        exp = Experiment.init("cycled")

        new = self.client.get_experiment_by_name("cycled")
        assert new is not None
        self.assertNotEqual(new.experiment_id, old_id)
        self.assertEqual(self._run_experiment_id(exp), new.experiment_id)

    def test_delete_experiment_twice_is_a_no_op(self) -> None:
        self.client.create_experiment("twice")

        delete_experiment("twice")
        delete_experiment("twice")

        self.assertIsNone(self.client.get_experiment_by_name("twice"))


if __name__ == "__main__":
    unittest.main()
