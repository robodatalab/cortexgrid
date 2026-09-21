from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

from cortexgrid.experiment import list_experiments, set_instance
from cortexgrid.jobs import JobLifecycle
from cortexgrid_ui.backend.main import app
from cortexgrid_ui.backend.streams import experiments_stream
from cortexgrid_ui.backend.streams.experiments_stream import Run

from tests.fakes import (
    FakeMlflowClient,
    FakeMlflowExperiment,
    FakeMlflowRun,
    FakeNotesDB,
    FakeRay,
    FakeS3,
    FakeState,
)


def _patched_infra(
    s3: FakeS3, mlflow: FakeMlflowClient, ray: FakeRay, db: FakeNotesDB
) -> ExitStack:
    """Patch every adapter to real infrastructure used by the request path."""
    stack = ExitStack()
    stack.enter_context(patch("cortexgrid.s3_util.get_s3_client", return_value=s3))
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
        patch(
            "cortexgrid.ray_util.get_ray_job_submission_client",
            return_value=ray,
        )
    )
    stack.enter_context(patch("psycopg.connect", return_value=db))
    stack.enter_context(
        patch(
            "cortexgrid_ui.backend.models.notes.get_secret",
            return_value="postgres://test",
        )
    )
    return stack


def _seed_cache(runs: list[Run]) -> None:
    by_exp: dict[str, dict[str, Run]] = {}
    for r in runs:
        by_exp.setdefault(r.experiment_name, {})[r.run_name] = r
    for exp_name, items in by_exp.items():
        experiments_stream.runs_cache.set(exp_name, items)
        experiments_stream.runs_refresher.pin(exp_name)


def _reset_stream() -> None:
    experiments_stream.runs_refresher._listeners.clear()
    experiments_stream.experiments_meta_refresher._listeners.clear()
    experiments_stream.runs_cache._data.clear()
    experiments_stream.experiments_meta_cache._data.clear()


def _make_run(experiment: str, run_id: str, run_name: str) -> Run:
    return Run(experiment_name=experiment, run_id=run_id, run_name=run_name, jobs=[])


def _record_run(
    state: FakeState,
    mlflow: FakeMlflowClient,
    experiment: str,
    run_id: str,
    run_name: str,
) -> None:
    """A run as Experiment.init leaves it: cortexgrid's records of the run and
    its experiment, each mapped onto its MLflow counterpart."""
    experiment_id = f"mlflow-{experiment}"
    if experiment not in state.experiments:
        state.seed_experiment(experiment, mlflow_experiment_id=experiment_id)
        mlflow.experiments.append(
            FakeMlflowExperiment(experiment_id=experiment_id, name=experiment)
        )
    state.seed_run(run_id, run_name, experiment_name=experiment)
    mlflow.runs.append(
        FakeMlflowRun(run_id=run_id, run_name=run_name, experiment_id=experiment_id)
    )


def _record_job(state: FakeState, s3: FakeS3, run_id: str, job_id: str) -> None:
    """A submitted job: its code tarball in S3 and its lifecycle recorded."""
    s3.objects[f"job/{job_id}/project_code_root.tar.gz"] = b"code"
    state.seed_job(
        JobLifecycle(
            experiment_name=state.runs[run_id]["experiment_name"],
            run_id=run_id,
            job_id=job_id,
        )
    )


class TestDeleteRunEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.state = FakeState().install(self)
        set_instance(None)
        _reset_stream()
        experiments_stream.experiments_meta_refresher.pin(
            experiments_stream.META_TOPIC
        )
        self.addCleanup(_reset_stream)
        self.addCleanup(set_instance, None)

    def _build_world(self) -> tuple[FakeS3, FakeMlflowClient, FakeRay, FakeNotesDB]:
        mlflow = FakeMlflowClient()
        _record_run(self.state, mlflow, "alpha", "run-1", "alpha-run")
        _record_run(self.state, mlflow, "beta", "run-2", "beta-run")
        s3 = FakeS3()
        _record_job(self.state, s3, "run-1", "j1")
        _record_job(self.state, s3, "run-2", "j2")
        ray = FakeRay({"run-1-j1-0": "RUNNING", "run-2-j2-0": "RUNNING"})
        db = FakeNotesDB(
            run_notes=[
                {"id": "n1", "run_id": "run-1", "body": "x"},
                {"id": "n2", "run_id": "run-1", "body": "y"},
                {"id": "n3", "run_id": "run-2", "body": "kept"},
            ]
        )
        return s3, mlflow, ray, db

    def _seed_full_cache(self) -> None:
        _seed_cache(
            [
                _make_run("alpha", "run-1", "alpha-run"),
                _make_run("beta", "run-2", "beta-run"),
            ]
        )

    def test_deleted_run_is_gone_from_listings_cache_s3_and_ray(self) -> None:
        s3, mlflow, ray, db = self._build_world()
        self._seed_full_cache()

        with _patched_infra(s3, mlflow, ray, db):
            response = self.client.delete("/api/runs/run-1")

            self.assertEqual(response.status_code, 200)
            self.assertNotIn("run-1", [e.run_id for e in list_experiments()])

        self.assertNotIn(
            "alpha-run", experiments_stream.runs_cache.get("alpha")
        )
        self.assertEqual(
            [k for k in s3.objects if k.startswith("job/j1/")], []
        )
        self.assertEqual(ray.jobs["run-1-j1-0"].status, "STOPPED")

    def test_deleting_run_purges_its_notes_from_the_database(self) -> None:
        s3, mlflow, ray, db = self._build_world()
        self._seed_full_cache()

        with _patched_infra(s3, mlflow, ray, db):
            self.client.delete("/api/runs/run-1")

        self.assertEqual([r["run_id"] for r in db.run_notes], ["run-2"])

    def test_other_runs_are_untouched_by_deleting_run(self) -> None:
        s3, mlflow, ray, db = self._build_world()
        self._seed_full_cache()

        with _patched_infra(s3, mlflow, ray, db):
            self.client.delete("/api/runs/run-1")

            self.assertIn("run-2", [e.run_id for e in list_experiments()])

        self.assertIn(
            "beta-run", experiments_stream.runs_cache.get("beta")
        )
        self.assertIn("job/j2/project_code_root.tar.gz", s3.objects)
        self.assertEqual(ray.jobs["run-2-j2-0"].status, "RUNNING")

    def test_deletes_run_when_cache_has_not_yet_picked_it_up(self) -> None:
        """Cache mid-refresh: run is recorded but cache is empty."""
        s3, mlflow, ray, db = self._build_world()

        with _patched_infra(s3, mlflow, ray, db):
            response = self.client.delete("/api/runs/run-1")

            self.assertEqual(response.status_code, 200)
            self.assertNotIn("run-1", [e.run_id for e in list_experiments()])

        self.assertEqual([k for k in s3.objects if k.startswith("job/j1/")], [])
        self.assertEqual(ray.jobs["run-1-j1-0"].status, "STOPPED")
        self.assertEqual([r["run_id"] for r in db.run_notes], ["run-2"])


class TestDeleteExperimentEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.state = FakeState().install(self)
        set_instance(None)
        _reset_stream()
        experiments_stream.experiments_meta_refresher.pin(
            experiments_stream.META_TOPIC
        )
        self.addCleanup(_reset_stream)
        self.addCleanup(set_instance, None)

    def _build_world(self) -> tuple[FakeS3, FakeMlflowClient, FakeRay, FakeNotesDB]:
        mlflow = FakeMlflowClient()
        _record_run(self.state, mlflow, "alpha", "run-1", "alpha-1")
        _record_run(self.state, mlflow, "alpha", "run-2", "alpha-2")
        _record_run(self.state, mlflow, "beta", "run-3", "beta-1")
        s3 = FakeS3()
        _record_job(self.state, s3, "run-1", "j1")
        _record_job(self.state, s3, "run-2", "j2")
        _record_job(self.state, s3, "run-3", "j3")
        ray = FakeRay(
            {
                "run-1-j1-0": "RUNNING",
                "run-2-j2-0": "RUNNING",
                "run-3-j3-0": "RUNNING",
            }
        )
        db = FakeNotesDB(
            run_notes=[
                {"id": "n1", "run_id": "run-1", "body": "x"},
                {"id": "n2", "run_id": "run-2", "body": "y"},
                {"id": "n3", "run_id": "run-3", "body": "kept"},
            ],
            experiment_notes=[
                {"id": "e1n", "experiment_name": "alpha", "body": "x"},
                {"id": "e2n", "experiment_name": "beta", "body": "kept"},
            ],
        )
        return s3, mlflow, ray, db

    def _seed_full_cache(self) -> None:
        _seed_cache(
            [
                _make_run("alpha", "run-1", "alpha-1"),
                _make_run("alpha", "run-2", "alpha-2"),
                _make_run("beta", "run-3", "beta-1"),
            ]
        )

    def test_deleted_experiment_is_gone_from_listings_cache_s3_and_ray(self) -> None:
        s3, mlflow, ray, db = self._build_world()
        self._seed_full_cache()

        with _patched_infra(s3, mlflow, ray, db):
            response = self.client.delete("/api/experiments/alpha")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                [e.experiment_name for e in list_experiments()], ["beta"]
            )

        self.assertEqual(experiments_stream.runs_cache.get("alpha"), {})
        self.assertEqual(
            set(experiments_stream.runs_cache.get("beta").keys()), {"beta-1"}
        )

        self.assertEqual([k for k in s3.objects if k.startswith("job/j1/")], [])
        self.assertEqual([k for k in s3.objects if k.startswith("job/j2/")], [])
        self.assertEqual(ray.jobs["run-1-j1-0"].status, "STOPPED")
        self.assertEqual(ray.jobs["run-2-j2-0"].status, "STOPPED")

    def test_deleting_experiment_purges_its_experiment_notes(self) -> None:
        s3, mlflow, ray, db = self._build_world()
        self._seed_full_cache()

        with _patched_infra(s3, mlflow, ray, db):
            self.client.delete("/api/experiments/alpha")

        self.assertEqual(
            [r["experiment_name"] for r in db.experiment_notes], ["beta"]
        )

    def test_deleting_experiment_purges_run_notes_for_all_its_runs(self) -> None:
        s3, mlflow, ray, db = self._build_world()
        self._seed_full_cache()

        with _patched_infra(s3, mlflow, ray, db):
            self.client.delete("/api/experiments/alpha")

        self.assertEqual([r["run_id"] for r in db.run_notes], ["run-3"])

    def test_other_experiments_are_untouched(self) -> None:
        s3, mlflow, ray, db = self._build_world()
        self._seed_full_cache()

        with _patched_infra(s3, mlflow, ray, db):
            self.client.delete("/api/experiments/alpha")

            self.assertIn("beta", [e.experiment_name for e in list_experiments()])

        self.assertIn("job/j3/project_code_root.tar.gz", s3.objects)
        self.assertEqual(ray.jobs["run-3-j3-0"].status, "RUNNING")
        self.assertEqual([r["run_id"] for r in db.run_notes], ["run-3"])

    def test_deletes_experiment_when_cache_has_not_picked_it_up_at_all(
        self,
    ) -> None:
        """Cache mid-refresh: experiment is recorded but cache is empty."""
        s3, mlflow, ray, db = self._build_world()

        with _patched_infra(s3, mlflow, ray, db):
            response = self.client.delete("/api/experiments/alpha")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                [e.experiment_name for e in list_experiments()], ["beta"]
            )

        self.assertEqual([k for k in s3.objects if k.startswith("job/j1/")], [])
        self.assertEqual([k for k in s3.objects if k.startswith("job/j2/")], [])
        self.assertEqual(ray.jobs["run-1-j1-0"].status, "STOPPED")
        self.assertEqual(ray.jobs["run-2-j2-0"].status, "STOPPED")
        self.assertEqual([r["run_id"] for r in db.run_notes], ["run-3"])
        self.assertEqual(
            [r["experiment_name"] for r in db.experiment_notes], ["beta"]
        )

    def test_deletes_experiment_when_cache_has_only_some_of_its_runs(
        self,
    ) -> None:
        """Cache mid-refresh: cache shows run-1 only, the records hold run-1 + run-2.

        The endpoint must enumerate runs from the run records (the source of
        truth) rather than the cache, otherwise run-2's notes leak.
        """
        s3, mlflow, ray, db = self._build_world()
        _seed_cache(
            [
                _make_run("alpha", "run-1", "alpha-1"),
                _make_run("beta", "run-3", "beta-1"),
            ]
        )

        with _patched_infra(s3, mlflow, ray, db):
            self.client.delete("/api/experiments/alpha")

        self.assertEqual([r["run_id"] for r in db.run_notes], ["run-3"])
        self.assertEqual(
            [k for k in s3.objects if k.startswith("job/j2/")], []
        )
        self.assertEqual(ray.jobs["run-2-j2-0"].status, "STOPPED")


if __name__ == "__main__":
    unittest.main()
