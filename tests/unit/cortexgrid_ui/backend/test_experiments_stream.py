from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from cortexgrid.experiment import DELETE_REQUESTED_TAG
from cortexgrid.jobs import JobLifecycle
from cortexgrid_ui.backend.streams.experiments_stream import (
    poll_experiments_meta,
    poll_runs,
)

from tests.fakes import (
    FakeArtifact,
    FakeMlflowClient,
    FakeMlflowExperiment,
    FakeMlflowRun,
    FakeRay,
)


_HELPER = "cortexgrid_ui.backend.streams.job_status"


def _patched_infra(
    mlflow: FakeMlflowClient,
    ray: FakeRay,
    jobs: dict[str, list[JobLifecycle]] | None = None,
) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(
        patch("cortexgrid.experiment.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexgrid.experiment.get_mlflow_tracking_uri", return_value="")
    )
    jobs = jobs or {}
    stack.enter_context(
        patch(
            f"{_HELPER}.list_experiment_run_job_ids",
            side_effect=lambda run_id: [j.job_id for j in jobs.get(run_id, [])],
        )
    )
    stack.enter_context(
        patch(
            f"{_HELPER}.load_job",
            side_effect=lambda run_id, job_id: next(
                (j for j in jobs.get(run_id, []) if j.job_id == job_id), None
            ),
        )
    )
    stack.enter_context(
        patch(
            "cortexgrid.ray_util.get_ray_job_submission_client", return_value=ray
        )
    )
    return stack


class TestExperimentsStreamRespectsDeletion(unittest.TestCase):
    """What was asked to go must not come back on the next poll."""

    def test_experiment_on_its_way_out_is_not_reported(self) -> None:
        going = FakeMlflowExperiment(
            experiment_id="e1",
            name="alpha__deleted__e1",
            tags={DELETE_REQUESTED_TAG: "true"},
        )
        staying = FakeMlflowExperiment(experiment_id="e2", name="beta")
        mlflow = FakeMlflowClient().seed([going, staying], [], {})

        with _patched_infra(mlflow, FakeRay()):
            reported = poll_experiments_meta(None)

        self.assertEqual(list(reported), ["beta"])

    def test_experiment_meta_still_carries_what_the_tree_shows(self) -> None:
        exp = FakeMlflowExperiment(
            experiment_id="e1", name="alpha", creation_time=1700000000000
        )
        mlflow = FakeMlflowClient().seed([exp], [], {})

        with _patched_infra(mlflow, FakeRay()):
            reported = poll_experiments_meta(None)

        self.assertEqual(reported["alpha"].created_at_ms, 1700000000000)

    def test_run_on_its_way_out_is_not_reported(self) -> None:
        exp = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        going = FakeMlflowRun(
            run_id="run-1",
            run_name="alpha-1",
            experiment_id="e1",
            tags={DELETE_REQUESTED_TAG: "true"},
        )
        staying = FakeMlflowRun(
            run_id="run-2", run_name="alpha-2", experiment_id="e1"
        )
        mlflow = FakeMlflowClient().seed(
            [exp], [going, staying], {"run-1": [], "run-2": []}
        )

        with _patched_infra(mlflow, FakeRay()):
            reported = poll_runs("alpha")

        self.assertEqual(list(reported), ["alpha-2"])

    def test_run_carries_its_jobs_and_times(self) -> None:
        exp = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        run = FakeMlflowRun(run_id="run-1", run_name="alpha-1", experiment_id="e1")
        mlflow = FakeMlflowClient().seed([exp], [run], {"run-1": []})
        job = JobLifecycle(experiment_name="alpha", run_id="run-1", job_id="j1")

        with _patched_infra(
            mlflow, FakeRay({"run-1-j1-0": "RUNNING"}), {"run-1": [job]}
        ):
            reported = poll_runs("alpha")

        self.assertEqual(
            [(j.job_id, j.status) for j in reported["alpha-1"].jobs],
            [("j1", "running")],
        )

    def test_the_tree_shows_a_job_on_its_way_out_as_deleting(self) -> None:
        """The same word the jobs table uses: one status, every view."""
        exp = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        run = FakeMlflowRun(run_id="run-1", run_name="alpha-1", experiment_id="e1")
        mlflow = FakeMlflowClient().seed([exp], [run], {"run-1": []})
        job = JobLifecycle(
            experiment_name="alpha",
            run_id="run-1",
            job_id="j1",
            delete_requested=True,
        )

        with _patched_infra(
            mlflow, FakeRay({"run-1-j1-0": "RUNNING"}), {"run-1": [job]}
        ):
            reported = poll_runs("alpha")

        self.assertEqual(
            [j.status for j in reported["alpha-1"].jobs], ["deleting"]
        )


if __name__ == "__main__":
    unittest.main()
