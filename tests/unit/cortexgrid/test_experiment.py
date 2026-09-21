from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mlflow.tracking import MlflowClient

from cortexgrid.experiment import (
    Experiment,
    delete_experiment,
    get_experiment_by_run_name,
    set_instance,
)
from cortexgrid.jobs import JobLifecycle

from tests.fakes import FakeRay, FakeS3, FakeState


class TestExperiment(unittest.TestCase):
    def setUp(self) -> None:
        set_instance(None)
        self.records = FakeState().install(self)
        self.fake_mlflow = MagicMock()
        self.fake_mlflow.create_experiment.return_value = "e1"
        self.fake_mlflow.create_run.return_value = MagicMock(
            info=MagicMock(run_id="run-1")
        )
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
        self.assertEqual(exp.run_id, "run-1")
        self.assertIs(Experiment.get_instance(), exp)

    def test_init_records_the_experiment_and_run(self) -> None:
        exp = Experiment.init("my-exp")

        self.assertEqual(
            self.records.experiments["my-exp"]["mlflow_experiment_id"], "e1"
        )
        self.assertEqual(self.records.runs["run-1"]["experiment_name"], "my-exp")
        self.fake_mlflow.create_run.assert_called_once_with(
            experiment_id="e1", run_name=exp.run_name()
        )

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

    def test_run_name_is_the_recorded_run_name(self) -> None:
        self.records.seed_run("run-xyz", run_name="boogey-46", experiment_name="my-exp")

        exp = Experiment.from_experiment("my-exp", "run-xyz")

        self.assertEqual(exp.run_name(), "boogey-46")

    def test_get_experiment_by_run_name_returns_matching_experiment(self) -> None:
        self.records.seed_run("r-1", run_name="happy-12", experiment_name="evals")
        self.records.seed_run("r-7", run_name="boogey-46", experiment_name="trainers")

        result = get_experiment_by_run_name("boogey-46")

        self.assertEqual(result, Experiment("trainers", "r-7"))

    def test_get_experiment_by_run_name_raises_when_no_runs_match(self) -> None:
        self.records.seed_run("r-1", run_name="happy-12", experiment_name="evals")
        with self.assertRaises(ValueError):
            get_experiment_by_run_name("missing")

    def test_get_experiment_by_run_name_raises_when_no_experiments_exist(self) -> None:
        with self.assertRaises(ValueError):
            get_experiment_by_run_name("anything")

    def test_get_jobs_lists_cortexgrid_job_ids(self) -> None:
        self.records.seed_run("run-xyz", experiment_name="my-exp")
        self.records.seed_run("run-other", experiment_name="my-exp")
        for run_id, job_id in (
            ("run-xyz", "job-1"),
            ("run-xyz", "job-2"),
            ("run-other", "job-3"),
        ):
            self.records.seed_job(
                JobLifecycle(experiment_name="my-exp", run_id=run_id, job_id=job_id)
            )

        exp = Experiment.from_experiment("my-exp", "run-xyz")
        result = exp.get_jobs()

        self.assertEqual(result, ["job-1", "job-2"])


class TestDeletedExperimentNames(unittest.TestCase):
    """Against a real MLflow store (SQLite), because its rules on deleted
    experiments are what these paths work around: a deleted experiment keeps
    its name reserved and cannot take new runs."""

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
        self.records = FakeState().install(self)
        for p in (
            patch(
                "cortexgrid.experiment.get_mlflow_tracking_uri",
                return_value=self.tracking_uri,
            ),
            patch("cortexgrid.s3_util.get_s3_client", return_value=FakeS3()),
            patch("cortexgrid.s3_util.get_s3_bucket", return_value="test-bucket"),
            patch(
                "cortexgrid.ray_util.get_ray_job_submission_client",
                return_value=FakeRay(),
            ),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _run_experiment_id(self, exp: Experiment) -> str:
        return self.client.get_run(exp.run_id).info.experiment_id

    def _mlflow_experiment_id(self, name: str) -> str:
        return self.records.experiments[name]["mlflow_experiment_id"]

    def test_init_creates_an_mlflow_experiment_of_the_same_name(self) -> None:
        exp = Experiment.init("fresh")

        new = self.client.get_experiment(self._mlflow_experiment_id("fresh"))
        self.assertEqual(new.name, "fresh")
        self.assertEqual(self._run_experiment_id(exp), new.experiment_id)

    def test_init_adds_a_run_to_the_mlflow_experiment_of_an_existing_experiment(
        self,
    ) -> None:
        first = Experiment.init("reused")
        Experiment.close()

        second = Experiment.init("reused")

        experiment_id = self._mlflow_experiment_id("reused")
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(self._run_experiment_id(first), experiment_id)
        self.assertEqual(self._run_experiment_id(second), experiment_id)

    def test_init_suffixes_a_name_a_deleted_mlflow_experiment_still_holds(
        self,
    ) -> None:
        # Deleted outside cortexgrid (e.g. the MLflow UI), so still under its name.
        old_id = self.client.create_experiment("reborn")
        self.client.delete_experiment(old_id)

        exp = Experiment.init("reborn")

        new = self.client.get_experiment(self._mlflow_experiment_id("reborn"))
        self.assertNotEqual(new.experiment_id, old_id)
        self.assertRegex(new.name, r"^reborn__[0-9a-f]{8}$")
        self.assertEqual(new.lifecycle_stage, "active")
        self.assertEqual(self._run_experiment_id(exp), new.experiment_id)

    def test_delete_experiment_soft_deletes_the_mlflow_experiment_under_its_name(
        self,
    ) -> None:
        exp = Experiment.init("released")
        old_id = self._mlflow_experiment_id("released")

        delete_experiment("released")

        old = self.client.get_experiment(old_id)
        self.assertEqual(old.lifecycle_stage, "deleted")
        self.assertEqual(old.name, "released")
        self.assertNotIn("released", self.records.experiments)
        self.assertNotIn(exp.run_id, self.records.runs)

    def test_init_after_delete_experiment_creates_a_new_experiment(self) -> None:
        Experiment.init("cycled")
        Experiment.close()
        old_id = self._mlflow_experiment_id("cycled")
        delete_experiment("cycled")

        exp = Experiment.init("cycled")

        new = self.client.get_experiment(self._mlflow_experiment_id("cycled"))
        self.assertNotEqual(new.experiment_id, old_id)
        self.assertRegex(new.name, r"^cycled__[0-9a-f]{8}$")
        self.assertEqual(self._run_experiment_id(exp), new.experiment_id)

    def test_delete_experiment_twice_is_a_no_op(self) -> None:
        Experiment.init("twice")
        old_id = self._mlflow_experiment_id("twice")

        delete_experiment("twice")
        delete_experiment("twice")

        self.assertNotIn("twice", self.records.experiments)
        self.assertEqual(self.client.get_experiment(old_id).lifecycle_stage, "deleted")


if __name__ == "__main__":
    unittest.main()
