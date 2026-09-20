from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from parameterized import parameterized

from cortexgrid.experiment import (
    DELETE_REQUESTED_TAG,
    Experiment,
    delete_experiment,
    delete_run,
    experiment_has_active_runs,
    experiments_pending_deletion,
    get_experiment_by_name,
    get_experiment_by_run_name,
    list_experiments,
    list_run_ids_in_experiment,
    runs_pending_deletion,
    search_experiments,
    search_runs,
    set_instance,
)

from tests.fakes import (
    FakeArtifact,
    FakeMlflowClient,
    FakeMlflowExperiment,
    FakeMlflowRun,
    FakeRay,
    FakeS3,
)


def _patched_infra(s3: FakeS3, mlflow: FakeMlflowClient, ray: FakeRay) -> ExitStack:
    """Patch every adapter that reaches real infrastructure."""
    stack = ExitStack()
    stack.enter_context(
        patch("cortexgrid.s3_util.get_s3_client", return_value=s3)
    )
    stack.enter_context(
        patch("cortexgrid.s3_util.get_s3_bucket", return_value="test-bucket")
    )
    stack.enter_context(
        patch("cortexgrid.experiment.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexgrid.experiment.get_mlflow_tracking_uri", return_value="")
    )
    stack.enter_context(
        patch("cortexgrid.jobs.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexgrid.jobs.get_mlflow_tracking_uri", return_value="")
    )
    stack.enter_context(
        patch("cortexgrid.model_storage.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch(
            "cortexgrid.model_storage.get_mlflow_tracking_uri", return_value=""
        )
    )
    stack.enter_context(
        patch(
            "cortexgrid.ray_util.get_ray_job_submission_client",
            return_value=ray,
        )
    )
    return stack


def _experiment_with_run_and_jobs(
    experiment_name: str,
    experiment_id: str,
    run_id: str,
    run_name: str,
    job_ids: list[str],
) -> tuple[FakeMlflowExperiment, FakeMlflowRun, dict[str, list[FakeArtifact]]]:
    exp = FakeMlflowExperiment(experiment_id=experiment_id, name=experiment_name)
    run = FakeMlflowRun(
        run_id=run_id, run_name=run_name, experiment_id=experiment_id
    )
    artifacts = {run_id: [FakeArtifact(path=f"job/{j}", is_dir=True) for j in job_ids]}
    return exp, run, artifacts


def _seed_job_package(s3: FakeS3, job_id: str) -> None:
    """The artifacts the control plane reads when launching a worker."""
    s3.objects[f"job/{job_id}/project_code_root.tar.gz"] = b"code"
    s3.objects[f"job/{job_id}/manifest.json"] = b"{}"
    s3.objects[f"job/{job_id}/lifecycle.json"] = b"{}"


def _tagged(record: FakeMlflowRun | FakeMlflowExperiment) -> bool:
    return record.tags.get(DELETE_REQUESTED_TAG) == "true"


class TestDeleteRun(unittest.TestCase):
    """Deleting a run records intent. The control plane does the rest."""

    def setUp(self) -> None:
        set_instance(None)
        self.addCleanup(set_instance, None)

    def test_run_is_tagged_for_deletion(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_run("run-1")

        self.assertTrue(_tagged(run))

    def test_every_job_in_the_run_is_asked_to_go(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", ["j1", "j2"]
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            with patch(
                "cortexgrid.experiment.request_run_jobs_deletion"
            ) as request:
                delete_run("run-1")

        request.assert_called_once_with("run-1")

    def test_the_run_leaves_the_listings_at_once(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            self.assertIn("run-1", [e.run_id for e in list_experiments()])

            delete_run("run-1")

            self.assertNotIn("run-1", [e.run_id for e in list_experiments()])

    def test_the_control_plane_can_still_see_it(self) -> None:
        """It is the only thing that can: otherwise nothing would tear it down."""
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_run("run-1")
            visible = list_experiments(include_deleting=True)

        self.assertIn("run-1", [e.run_id for e in visible])

    def test_the_run_stops_answering_to_its_name(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_run("run-1")

            with self.assertRaises(ValueError):
                get_experiment_by_run_name("alpha-run")

    def test_nothing_is_stopped_or_wiped_here(self) -> None:
        """Ray and S3 belong to the control plane; this call must not touch them."""
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", ["j1"]
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)
        s3 = FakeS3()
        _seed_job_package(s3, "j1")
        ray = FakeRay({"run-1-j1-0": "RUNNING"})

        with _patched_infra(s3, mlflow, ray):
            delete_run("run-1")

        self.assertEqual(ray.jobs["run-1-j1-0"].status, "RUNNING")
        self.assertIn("job/j1/project_code_root.tar.gz", s3.objects)

    def test_the_run_record_is_left_for_the_control_plane_to_remove(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_run("run-1")

        self.assertEqual(run.lifecycle_stage, "active")

    def test_other_runs_are_untouched(self) -> None:
        exp_a, run_a, art_a = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", ["j1"]
        )
        exp_b, run_b, art_b = _experiment_with_run_and_jobs(
            "beta", "e2", "run-2", "beta-run", ["j2"]
        )
        mlflow = FakeMlflowClient().seed(
            [exp_a, exp_b], [run_a, run_b], {**art_a, **art_b}
        )

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_run("run-1")

            self.assertIn("run-2", [e.run_id for e in list_experiments()])

        self.assertFalse(_tagged(run_b))


class TestDeleteExperiment(unittest.TestCase):
    """Deleting an experiment frees its name at once and requests the rest."""

    def setUp(self) -> None:
        set_instance(None)
        self.addCleanup(set_instance, None)

    def test_the_name_is_free_the_moment_the_call_returns(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")

            self.assertIsNone(mlflow.get_experiment_by_name("alpha"))

    def test_the_experiment_is_renamed_and_tagged_not_removed(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")

        self.assertEqual(exp.name, "alpha__deleted__e1")
        self.assertTrue(_tagged(exp))
        self.assertEqual(exp.lifecycle_stage, "active")

    def test_every_run_of_the_experiment_is_requested(self) -> None:
        exp = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        runs = [
            FakeMlflowRun(run_id="run-1", run_name="alpha-1", experiment_id="e1"),
            FakeMlflowRun(run_id="run-2", run_name="alpha-2", experiment_id="e1"),
        ]
        mlflow = FakeMlflowClient().seed([exp], runs, {"run-1": [], "run-2": []})

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")

        self.assertTrue(all(_tagged(r) for r in runs))

    def test_the_experiment_leaves_the_listings_at_once(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")

            self.assertEqual(list_experiments(), [])

    def test_the_control_plane_can_still_see_it(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")
            visible = list_experiments(include_deleting=True)

        self.assertEqual([e.run_id for e in visible], ["run-1"])

    def test_nothing_is_stopped_or_wiped_here(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", ["j1"]
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)
        s3 = FakeS3()
        _seed_job_package(s3, "j1")
        ray = FakeRay({"run-1-j1-0": "RUNNING"})

        with _patched_infra(s3, mlflow, ray):
            delete_experiment("alpha")

        self.assertEqual(ray.jobs["run-1-j1-0"].status, "RUNNING")
        self.assertIn("job/j1/project_code_root.tar.gz", s3.objects)

    def test_deleting_the_same_experiment_twice_is_a_no_op(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")
            delete_experiment("alpha")

        self.assertEqual(exp.name, "alpha__deleted__e1")

    def test_other_experiments_remain_in_listings(self) -> None:
        exp_a, run_a, art_a = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        exp_b, run_b, art_b = _experiment_with_run_and_jobs(
            "beta", "e2", "run-2", "beta-run", []
        )
        mlflow = FakeMlflowClient().seed(
            [exp_a, exp_b], [run_a, run_b], {**art_a, **art_b}
        )

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")
            remaining = [e.experiment_name for e in list_experiments()]

        self.assertEqual(remaining, ["beta"])


class TestDeletedRecordsAreHiddenFromEveryReader(unittest.TestCase):
    """One record, every reader: what is hidden must be hidden everywhere.

    The rule these pin down is that a record asked to be deleted leaves
    every lookup at once, and that the control plane's own views are the
    single deliberate exception.
    """

    def setUp(self) -> None:
        set_instance(None)
        self.addCleanup(set_instance, None)

    def _world(self) -> tuple[FakeMlflowClient, FakeMlflowExperiment, FakeMlflowRun]:
        exp = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        going = FakeMlflowRun(
            run_id="run-1", run_name="alpha-1", experiment_id="e1"
        )
        staying = FakeMlflowRun(
            run_id="run-2", run_name="alpha-2", experiment_id="e1"
        )
        mlflow = FakeMlflowClient().seed(
            [exp], [going, staying], {"run-1": [], "run-2": []}
        )
        return mlflow, exp, going

    @parameterized.expand(
        [
            ("list_experiments", lambda: [e.run_id for e in list_experiments()]),
            ("search_runs", lambda: [r.info.run_id for r in search_runs(["e1"])]),
            ("list_run_ids_in_experiment", lambda: list_run_ids_in_experiment("alpha")),
        ]
    )
    def test_a_deleted_run_is_absent_from(self, _name, read) -> None:
        mlflow, _exp, _run = self._world()

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_run("run-1")
            visible = read()

        self.assertEqual(visible, ["run-2"])

    def test_a_deleted_run_stops_answering_to_its_name(self) -> None:
        mlflow, _exp, _run = self._world()

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_run("run-1")

            with self.assertRaises(ValueError):
                get_experiment_by_run_name("alpha-1")
            self.assertEqual(
                get_experiment_by_run_name("alpha-2").run_id, "run-2"
            )

    def test_the_control_plane_is_the_one_reader_that_still_sees_it(self) -> None:
        mlflow, _exp, _run = self._world()

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_run("run-1")

            self.assertEqual(runs_pending_deletion(), ["run-1"])
            self.assertIn(
                "run-1",
                [r.info.run_id for r in search_runs(["e1"], include_deleting=True)],
            )
            self.assertIn(
                "run-1", [e.run_id for e in list_experiments(include_deleting=True)]
            )
            # The experiment cannot go while a run of it is still there.
            self.assertTrue(experiment_has_active_runs("e1"))

    @parameterized.expand(
        [
            ("search_experiments", lambda: [e.name for e in search_experiments()]),
            (
                "list_experiments",
                lambda: [e.experiment_name for e in list_experiments()],
            ),
        ]
    )
    def test_a_deleted_experiment_is_absent_from(self, _name, read) -> None:
        exp_a = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        exp_b = FakeMlflowExperiment(experiment_id="e2", name="beta")
        run_a = FakeMlflowRun(run_id="run-1", run_name="alpha-1", experiment_id="e1")
        run_b = FakeMlflowRun(run_id="run-2", run_name="beta-1", experiment_id="e2")
        mlflow = FakeMlflowClient().seed(
            [exp_a, exp_b], [run_a, run_b], {"run-1": [], "run-2": []}
        )

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")
            visible = read()

        self.assertEqual(visible, ["beta"])

    def test_a_deleted_experiment_stops_answering_to_its_name(self) -> None:
        mlflow, _exp, _run = self._world()

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")

            self.assertIsNone(get_experiment_by_name("alpha"))
            self.assertEqual(list_run_ids_in_experiment("alpha"), [])

    def test_a_deleted_experiment_is_not_even_found_under_its_new_name(self) -> None:
        """Renaming frees the name; the tag is what hides the record."""
        mlflow, _exp, _run = self._world()

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")

            self.assertIsNone(get_experiment_by_name("alpha__deleted__e1"))
            self.assertIsNotNone(
                get_experiment_by_name("alpha__deleted__e1", include_deleting=True)
            )
            self.assertEqual(experiments_pending_deletion(), ["e1"])

    def test_creating_into_an_experiment_being_deleted_is_refused(self) -> None:
        """Its runs would be torn down with it, so this must not go quietly."""
        mlflow, exp, _run = self._world()

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")
            # Only reachable through the name it was moved to; by its own
            # name a fresh experiment is created instead.
            with self.assertRaises(RuntimeError):
                Experiment.init(f"alpha__deleted__{exp.experiment_id}")

    def test_a_new_experiment_takes_the_name_of_the_one_being_deleted(self) -> None:
        """delete_experiment(name) then init(name) is always a fresh experiment."""
        mlflow, exp, _run = self._world()

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")
            Experiment.init("alpha")

            fresh = get_experiment_by_name("alpha")
            assert fresh is not None
            self.assertNotEqual(fresh.experiment_id, exp.experiment_id)
            self.assertEqual([e.name for e in search_experiments()], ["alpha"])


if __name__ == "__main__":
    unittest.main()
