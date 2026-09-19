from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

from cortexgrid_ui.backend.main import app
from cortexgrid_ui.backend.streams import jobs_stream
from cortexgrid_ui.backend.streams.jobs_stream import JobRow, poll_jobs

from tests.fakes import (
    FakeArtifact,
    FakeMlflowClient,
    FakeMlflowExperiment,
    FakeMlflowRun,
    FakeRay,
)


def _patched_infra(mlflow: FakeMlflowClient, ray: FakeRay) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(
        patch("cortexgrid_ui.backend.streams.jobs_stream.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch(
            "cortexgrid_ui.backend.streams.jobs_stream.get_mlflow_tracking_uri",
            return_value="",
        )
    )
    stack.enter_context(
        patch(
            "cortexgrid.ray_util.get_ray_job_submission_client",
            return_value=ray,
        )
    )
    return stack


def _world(
    jobs_by_run: dict[str, list[str]] | None = None,
) -> FakeMlflowClient:
    """One experiment per run, so ownership is visible in every row."""
    jobs_by_run = jobs_by_run if jobs_by_run is not None else {"run-1": ["j1"]}
    experiments = []
    runs = []
    artifacts: dict[str, list[FakeArtifact]] = {}
    for index, (run_id, job_ids) in enumerate(jobs_by_run.items(), start=1):
        experiments.append(
            FakeMlflowExperiment(experiment_id=f"e{index}", name=f"exp{index}")
        )
        runs.append(
            FakeMlflowRun(
                run_id=run_id,
                run_name=f"{run_id}-name",
                experiment_id=f"e{index}",
            )
        )
        artifacts[run_id] = [
            FakeArtifact(path=f"job/{job_id}", is_dir=True) for job_id in job_ids
        ]
    return FakeMlflowClient().seed(experiments, runs, artifacts)


def _rows(mlflow: FakeMlflowClient, ray: FakeRay) -> dict[str, JobRow]:
    with _patched_infra(mlflow, ray):
        return poll_jobs(jobs_stream.META_TOPIC)


class TestPollJobs(unittest.TestCase):
    def test_lists_a_job_with_the_experiment_and_run_that_own_it(self) -> None:
        rows = _rows(_world({"run-1": ["j1"]}), FakeRay())

        row = rows["run-1/j1"]
        self.assertEqual(row.job_id, "j1")
        self.assertEqual(row.experiment_name, "exp1")
        self.assertEqual(row.run_id, "run-1")
        self.assertEqual(row.run_name, "run-1-name")
        self.assertFalse(row.abandoned)

    def test_lists_the_jobs_of_every_run_of_every_experiment(self) -> None:
        rows = _rows(_world({"run-1": ["j1", "j2"], "run-2": ["j3"]}), FakeRay())

        self.assertEqual(
            sorted(rows), ["run-1/j1", "run-1/j2", "run-2/j3"]
        )

    def test_status_comes_from_the_latest_ray_attempt(self) -> None:
        ray = FakeRay({"run-1-j1-0": "FAILED", "run-1-j1-1": "RUNNING"})

        rows = _rows(_world({"run-1": ["j1"]}), ray)

        self.assertEqual(rows["run-1/j1"].status, "running")
        self.assertEqual(rows["run-1/j1"].ray_job_id, "run-1-j1-1")

    def test_latest_attempt_is_the_highest_numbered_one(self) -> None:
        """Attempt 10 is later than attempt 9, though it sorts before it."""
        ray = FakeRay({"run-1-j1-9": "FAILED", "run-1-j1-10": "SUCCEEDED"})

        rows = _rows(_world({"run-1": ["j1"]}), ray)

        self.assertEqual(rows["run-1/j1"].ray_job_id, "run-1-j1-10")
        self.assertEqual(rows["run-1/j1"].status, "finished")

    def test_job_ray_has_never_seen_is_pending(self) -> None:
        rows = _rows(_world({"run-1": ["j1"]}), FakeRay())

        self.assertEqual(rows["run-1/j1"].status, "pending")
        self.assertIsNone(rows["run-1/j1"].ray_job_id)

    def test_ray_submission_no_job_claims_is_listed_as_abandoned(self) -> None:
        # Submission ids read `<run_id>-<job_id>-<attempt>`, and an mlflow
        # run id is dash-free hex, so the owner of an abandoned job is still
        # recoverable from its name.
        ray = FakeRay({"a1b2c3-lost-fox-77-0": "RUNNING"})

        rows = _rows(_world({"run-1": ["j1"]}), ray)

        row = rows["ray/a1b2c3/lost-fox-77"]
        self.assertTrue(row.abandoned)
        self.assertEqual(row.job_id, "lost-fox-77")
        self.assertEqual(row.run_id, "a1b2c3")
        self.assertIsNone(row.experiment_name)
        self.assertIsNone(row.run_name)
        self.assertEqual(row.status, "running")

    def test_claimed_ray_submission_is_not_also_listed_as_abandoned(self) -> None:
        ray = FakeRay({"run-1-j1-0": "RUNNING"})

        rows = _rows(_world({"run-1": ["j1"]}), ray)

        self.assertEqual(sorted(rows), ["run-1/j1"])

    def test_an_older_attempt_does_not_count_as_abandoned(self) -> None:
        ray = FakeRay({"run-1-j1-0": "FAILED", "run-1-j1-1": "RUNNING"})

        rows = _rows(_world({"run-1": ["j1"]}), ray)

        self.assertEqual(sorted(rows), ["run-1/j1"])

    def test_attempts_of_one_abandoned_job_make_one_row(self) -> None:
        ray = FakeRay(
            {"a1b2c3-lost-fox-77-0": "FAILED", "a1b2c3-lost-fox-77-1": "RUNNING"}
        )

        rows = _rows(_world({"run-1": ["j1"]}), ray)

        row = rows["ray/a1b2c3/lost-fox-77"]
        self.assertEqual(sorted(rows), ["ray/a1b2c3/lost-fox-77", "run-1/j1"])
        self.assertEqual(row.status, "running")
        self.assertEqual(row.ray_job_id, "a1b2c3-lost-fox-77-1")

    def test_submission_this_cluster_did_not_name_is_left_out(self) -> None:
        """Not every ray job is a cortexgrid job; those are not ours to show."""
        ray = FakeRay({"someones-manual-submission": "RUNNING"})

        rows = _rows(_world({"run-1": ["j1"]}), ray)

        self.assertEqual(sorted(rows), ["run-1/j1"])

    def test_a_run_that_cannot_be_read_does_not_sink_the_whole_list(self) -> None:
        mlflow = _world({"run-1": ["j1"], "run-2": ["j2"]})
        broken = mlflow.list_artifacts

        def fail_for_run_1(run_id: str, path: str = "") -> list[FakeArtifact]:
            if run_id == "run-1":
                raise RuntimeError("mlflow is having a moment")
            return broken(run_id, path)

        with patch.object(mlflow, "list_artifacts", fail_for_run_1):
            rows = _rows(mlflow, FakeRay())

        self.assertEqual(sorted(rows), ["run-2/j2"])


def _reset_jobs_stream() -> None:
    jobs_stream.refresher._listeners.clear()
    jobs_stream.cache._data.clear()


def _seed_cache(rows: list[JobRow]) -> None:
    jobs_stream.cache.set(jobs_stream.META_TOPIC, {r.id: r for r in rows})
    jobs_stream.refresher.pin(jobs_stream.META_TOPIC)


def _row(job_id: str, run_id: str = "run-1", abandoned: bool = False) -> JobRow:
    return JobRow(
        id=(
            jobs_stream.abandoned_row_id(run_id, job_id)
            if abandoned
            else jobs_stream.row_id(run_id, job_id)
        ),
        job_id=job_id,
        status="running",
        experiment_name=None if abandoned else "exp1",
        run_id=run_id,
        run_name=None if abandoned else "run-1-name",
        ray_job_id=f"{run_id}-{job_id}-0",
        abandoned=abandoned,
    )


class TestDeleteJobEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        _reset_jobs_stream()
        self.addCleanup(_reset_jobs_stream)

    def test_deletes_the_job_that_was_asked_for(self) -> None:
        with patch("cortexgrid_ui.backend.main.delete_job") as delete:
            response = self.client.delete("/api/runs/run-1/jobs/j1")

        self.assertEqual(response.status_code, 200)
        delete.assert_called_once_with("run-1", "j1")

    def test_deleted_job_leaves_the_jobs_stream(self) -> None:
        _seed_cache([_row("j1"), _row("j2")])

        with patch("cortexgrid_ui.backend.main.delete_job"):
            self.client.delete("/api/runs/run-1/jobs/j1")

        self.assertEqual(
            sorted(jobs_stream.cache.get(jobs_stream.META_TOPIC)), ["run-1/j2"]
        )

    def test_reports_a_job_that_is_not_there(self) -> None:
        with patch(
            "cortexgrid_ui.backend.main.delete_job",
            side_effect=FileNotFoundError("no lifecycle"),
        ):
            response = self.client.delete("/api/runs/run-1/jobs/ghost")

        self.assertEqual(response.status_code, 404)

    def test_reports_a_job_ray_would_not_let_go_of(self) -> None:
        with patch(
            "cortexgrid_ui.backend.main.delete_job",
            side_effect=TimeoutError("did not stop"),
        ):
            response = self.client.delete("/api/runs/run-1/jobs/j1")

        self.assertEqual(response.status_code, 504)

    def test_a_job_that_could_not_be_deleted_stays_in_the_stream(self) -> None:
        _seed_cache([_row("j1")])

        with patch(
            "cortexgrid_ui.backend.main.delete_job",
            side_effect=TimeoutError("did not stop"),
        ):
            self.client.delete("/api/runs/run-1/jobs/j1")

        self.assertIn("run-1/j1", jobs_stream.cache.get(jobs_stream.META_TOPIC))


class TestDeleteAbandonedJobEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        _reset_jobs_stream()
        self.addCleanup(_reset_jobs_stream)

    def test_purges_every_ray_attempt_of_the_job(self) -> None:
        with patch("cortexgrid_ui.backend.main.purge_abandoned_job") as purge:
            response = self.client.delete(
                "/api/jobs/abandoned/a1b2c3/lost-fox-77"
            )

        self.assertEqual(response.status_code, 200)
        purge.assert_called_once_with("a1b2c3", "lost-fox-77")

    def test_purged_submission_leaves_the_jobs_stream(self) -> None:
        _seed_cache([_row("lost-fox-77", run_id="a1b2c3", abandoned=True)])

        with patch("cortexgrid_ui.backend.main.purge_abandoned_job"):
            self.client.delete("/api/jobs/abandoned/a1b2c3/lost-fox-77")

        self.assertEqual(jobs_stream.cache.get(jobs_stream.META_TOPIC), {})

    def test_reports_a_submission_ray_would_not_let_go_of(self) -> None:
        with patch(
            "cortexgrid_ui.backend.main.purge_abandoned_job",
            side_effect=TimeoutError("did not stop"),
        ):
            response = self.client.delete(
                "/api/jobs/abandoned/a1b2c3/lost-fox-77"
            )

        self.assertEqual(response.status_code, 504)


if __name__ == "__main__":
    unittest.main()
