from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from cortexgrid.experiment import DELETE_REQUESTED_TAG
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


def _patched_infra(mlflow: FakeMlflowClient, ray: FakeRay) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(
        patch("cortexgrid.experiment.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexgrid.experiment.get_mlflow_tracking_uri", return_value="")
    )
    stack.enter_context(
        patch(
            "cortexgrid_ui.backend.streams.experiments_stream.MlflowClient",
            return_value=mlflow,
        )
    )
    stack.enter_context(
        patch(
            "cortexgrid_ui.backend.streams.experiments_stream.get_mlflow_tracking_uri",
            return_value="",
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
        mlflow = FakeMlflowClient().seed(
            [exp], [run], {"run-1": [FakeArtifact(path="job/j1", is_dir=True)]}
        )

        with _patched_infra(mlflow, FakeRay({"run-1-j1-0": "RUNNING"})):
            reported = poll_runs("alpha")

        self.assertEqual(
            [(j.job_id, j.status) for j in reported["alpha-1"].jobs],
            [("j1", "running")],
        )


if __name__ == "__main__":
    unittest.main()
