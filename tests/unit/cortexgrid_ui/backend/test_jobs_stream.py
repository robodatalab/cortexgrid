from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

from cortexgrid.experiment import DELETE_REQUESTED_TAG
from cortexgrid.jobs import JobLifecycle
from cortexgrid_ui.backend.main import app
from cortexgrid_ui.backend.streams import jobs_stream
from cortexgrid_ui.backend.streams.jobs_stream import JobRow, poll_jobs

from tests.fakes import (
    FakeMlflowClient,
    FakeMlflowExperiment,
    FakeMlflowRun,
    FakeRay,
)

EXPERIMENT = "alpha"
RUN_ID = "run-1"


def _patched_infra(
    mlflow: FakeMlflowClient, ray: FakeRay, jobs: dict[str, list[JobLifecycle]]
) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(
        patch("cortexgrid.experiment.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexgrid.experiment.get_mlflow_tracking_uri", return_value="")
    )
    stack.enter_context(
        patch(
            "cortexgrid.ray_util.get_ray_job_submission_client", return_value=ray
        )
    )
    stack.enter_context(
        patch(
            "cortexgrid_ui.backend.streams.job_status.list_experiment_run_job_ids",
            side_effect=lambda run_id: [j.job_id for j in jobs.get(run_id, [])],
        )
    )
    stack.enter_context(
        patch(
            "cortexgrid_ui.backend.streams.job_status.load_job",
            side_effect=lambda run_id, job_id: next(
                (j for j in jobs.get(run_id, []) if j.job_id == job_id), None
            ),
        )
    )
    return stack


def _lifecycle(job_id: str, run_id: str = RUN_ID, **kwargs: object) -> JobLifecycle:
    return JobLifecycle(
        experiment_name=EXPERIMENT, run_id=run_id, job_id=job_id, **kwargs
    )


def _world(run_ids: list[str] | None = None) -> FakeMlflowClient:
    run_ids = run_ids or [RUN_ID]
    exp = FakeMlflowExperiment(experiment_id="e1", name=EXPERIMENT)
    runs = [
        FakeMlflowRun(run_id=r, run_name=f"{r}-name", experiment_id="e1")
        for r in run_ids
    ]
    return FakeMlflowClient().seed([exp], runs, {r: [] for r in run_ids})


def _rows(
    mlflow: FakeMlflowClient, ray: FakeRay, jobs: dict[str, list[JobLifecycle]]
) -> dict[str, JobRow]:
    with _patched_infra(mlflow, ray, jobs):
        return poll_jobs(jobs_stream.META_TOPIC)


class TestPollJobs(unittest.TestCase):
    def test_lists_a_job_with_the_experiment_and_run_that_own_it(self) -> None:
        rows = _rows(_world(), FakeRay(), {RUN_ID: [_lifecycle("j1")]})

        row = rows["run-1/j1"]
        self.assertEqual(row.job_id, "j1")
        self.assertEqual(row.experiment_name, EXPERIMENT)
        self.assertEqual(row.run_name, "run-1-name")
        self.assertFalse(row.abandoned)

    def test_status_comes_from_the_latest_ray_attempt(self) -> None:
        ray = FakeRay({"run-1-j1-0": "FAILED", "run-1-j1-1": "RUNNING"})

        rows = _rows(_world(), ray, {RUN_ID: [_lifecycle("j1")]})

        self.assertEqual(rows["run-1/j1"].status, "running")
        self.assertEqual(rows["run-1/j1"].ray_job_id, "run-1-j1-1")

    def test_latest_attempt_is_the_highest_numbered_one(self) -> None:
        """Attempt 10 is later than attempt 9, though it sorts before it."""
        ray = FakeRay({"run-1-j1-9": "FAILED", "run-1-j1-10": "SUCCEEDED"})

        rows = _rows(_world(), ray, {RUN_ID: [_lifecycle("j1")]})

        self.assertEqual(rows["run-1/j1"].status, "finished")

    def test_job_ray_has_never_seen_is_pending(self) -> None:
        rows = _rows(_world(), FakeRay(), {RUN_ID: [_lifecycle("j1")]})

        self.assertEqual(rows["run-1/j1"].status, "pending")

    def test_a_job_asked_to_go_reads_as_deleting(self) -> None:
        """The record overrides Ray: it is on its way out, whatever Ray says."""
        ray = FakeRay({"run-1-j1-0": "RUNNING"})

        rows = _rows(
            _world(), ray, {RUN_ID: [_lifecycle("j1", delete_requested=True)]}
        )

        self.assertEqual(rows["run-1/j1"].status, "deleting")

    def test_jobs_of_a_run_on_its_way_out_leave_the_table(self) -> None:
        """The gate hides the run, so its jobs go with it."""
        mlflow = _world()
        mlflow.runs[0].tags[DELETE_REQUESTED_TAG] = "true"

        rows = _rows(mlflow, FakeRay(), {RUN_ID: [_lifecycle("j1")]})

        self.assertEqual(rows, {})

    def test_ray_submission_no_job_claims_is_listed_as_abandoned(self) -> None:
        ray = FakeRay({"a1b2c3-lost-fox-77-0": "RUNNING"})

        rows = _rows(_world(), ray, {RUN_ID: [_lifecycle("j1")]})

        row = rows["ray/a1b2c3/lost-fox-77"]
        self.assertTrue(row.abandoned)
        self.assertEqual(row.job_id, "lost-fox-77")
        self.assertEqual(row.run_id, "a1b2c3")
        self.assertIsNone(row.experiment_name)

    def test_attempts_of_one_abandoned_job_make_one_row(self) -> None:
        ray = FakeRay(
            {"a1b2c3-lost-fox-77-0": "FAILED", "a1b2c3-lost-fox-77-1": "RUNNING"}
        )

        rows = _rows(_world(), ray, {})

        self.assertEqual(list(rows), ["ray/a1b2c3/lost-fox-77"])
        self.assertEqual(rows["ray/a1b2c3/lost-fox-77"].status, "running")

    def test_claimed_ray_submission_is_not_also_listed_as_abandoned(self) -> None:
        ray = FakeRay({"run-1-j1-0": "RUNNING"})

        rows = _rows(_world(), ray, {RUN_ID: [_lifecycle("j1")]})

        self.assertEqual(list(rows), ["run-1/j1"])

    def test_submission_this_cluster_did_not_name_is_left_out(self) -> None:
        """Not every ray job is a cortexgrid job; those are not ours to show."""
        ray = FakeRay({"someones-manual-submission": "RUNNING"})

        rows = _rows(_world(), ray, {})

        self.assertEqual(rows, {})

    def test_a_run_that_cannot_be_read_does_not_sink_the_whole_table(self) -> None:
        mlflow = _world(["run-1", "run-2"])

        def fail_for_run_1(run_id: str) -> list[str]:
            if run_id == "run-1":
                raise RuntimeError("mlflow is having a moment")
            return ["j2"]

        with _patched_infra(mlflow, FakeRay(), {"run-2": [_lifecycle("j2", run_id="run-2")]}):
            with patch(
                "cortexgrid_ui.backend.streams.job_status.list_experiment_run_job_ids",
                side_effect=fail_for_run_1,
            ):
                rows = poll_jobs(jobs_stream.META_TOPIC)

        self.assertEqual(list(rows), ["run-2/j2"])


def _reset_jobs_stream() -> None:
    jobs_stream.refresher._listeners.clear()
    jobs_stream.cache._data.clear()


def _seed_cache(rows: list[JobRow]) -> None:
    jobs_stream.cache.set(jobs_stream.META_TOPIC, {r.id: r for r in rows})
    jobs_stream.refresher.pin(jobs_stream.META_TOPIC)


def _row(job_id: str, status: str = "running") -> JobRow:
    return JobRow(
        id=jobs_stream.row_id(RUN_ID, job_id),
        job_id=job_id,
        status=status,
        experiment_name=EXPERIMENT,
        run_id=RUN_ID,
        run_name="run-1-name",
        ray_job_id=f"{RUN_ID}-{job_id}-0",
        abandoned=False,
    )


class TestDeleteJobEndpoint(unittest.TestCase):
    """The endpoint writes intent; the control plane does the work."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        _reset_jobs_stream()
        self.addCleanup(_reset_jobs_stream)

    def test_asks_for_the_job_to_be_deleted(self) -> None:
        with patch("cortexgrid_ui.backend.main.request_job_deletion") as request:
            response = self.client.delete(f"/api/runs/{RUN_ID}/jobs/j1")

        self.assertEqual(response.status_code, 200)
        request.assert_called_once_with(RUN_ID, "j1")

    def test_the_row_reads_as_deleting_without_waiting_for_a_sweep(self) -> None:
        _seed_cache([_row("j1"), _row("j2")])

        with patch("cortexgrid_ui.backend.main.request_job_deletion"):
            self.client.delete(f"/api/runs/{RUN_ID}/jobs/j1")

        rows = jobs_stream.cache.get(jobs_stream.META_TOPIC)
        self.assertEqual(rows["run-1/j1"].status, "deleting")
        self.assertEqual(rows["run-1/j2"].status, "running")

    def test_the_row_stays_until_the_control_plane_removes_it(self) -> None:
        """Nothing here deletes the record, so the row must not vanish."""
        _seed_cache([_row("j1")])

        with patch("cortexgrid_ui.backend.main.request_job_deletion"):
            self.client.delete(f"/api/runs/{RUN_ID}/jobs/j1")

        self.assertIn("run-1/j1", jobs_stream.cache.get(jobs_stream.META_TOPIC))

    def test_reports_a_job_that_is_not_there(self) -> None:
        with patch(
            "cortexgrid_ui.backend.main.request_job_deletion",
            side_effect=FileNotFoundError("no lifecycle"),
        ):
            response = self.client.delete(f"/api/runs/{RUN_ID}/jobs/ghost")

        self.assertEqual(response.status_code, 404)

    def test_nothing_is_stopped_or_wiped_here(self) -> None:
        """No Ray call, no S3 call: this endpoint only writes a latch."""
        ray = FakeRay({"run-1-j1-0": "RUNNING"})
        mlflow = _world()

        with _patched_infra(mlflow, ray, {RUN_ID: [_lifecycle("j1")]}):
            with patch("cortexgrid.jobs.MlflowClient", return_value=mlflow):
                with patch(
                    "cortexgrid.jobs.get_mlflow_tracking_uri", return_value=""
                ):
                    self.client.delete(f"/api/runs/{RUN_ID}/jobs/j1")

        self.assertEqual(ray.jobs["run-1-j1-0"].status, "RUNNING")


if __name__ == "__main__":
    unittest.main()
