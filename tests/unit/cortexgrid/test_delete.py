from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from cortexgrid.experiment import (
    delete_experiment,
    delete_run,
    list_experiments,
    set_instance,
)
from cortexgrid.jobs import JobLifecycle

from tests.fakes import (
    FakeMlflowClient,
    FakeMlflowExperiment,
    FakeMlflowRun,
    FakeRay,
    FakeS3,
    FakeState,
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
        patch(
            "cortexgrid.ray_util.get_ray_job_submission_client",
            return_value=ray,
        )
    )
    return stack


def _seed_run_with_jobs(
    records: FakeState,
    mlflow: FakeMlflowClient,
    experiment_name: str,
    experiment_id: str,
    run_id: str,
    job_ids: list[str],
) -> None:
    """A run and its jobs as cortexgrid records them, plus the MLflow
    experiment and run they map onto."""
    if experiment_name not in records.experiments:
        records.seed_experiment(experiment_name, mlflow_experiment_id=experiment_id)
        mlflow.experiments.append(
            FakeMlflowExperiment(experiment_id=experiment_id, name=experiment_name)
        )
    records.seed_run(run_id, experiment_name=experiment_name)
    mlflow.runs.append(
        FakeMlflowRun(run_id=run_id, run_name=run_id, experiment_id=experiment_id)
    )
    for job_id in job_ids:
        records.seed_job(
            JobLifecycle(experiment_name=experiment_name, run_id=run_id, job_id=job_id)
        )


def _seed_job_package(s3: FakeS3, job_id: str) -> None:
    """The artifacts the control plane reads when launching a worker."""
    s3.objects[f"job/{job_id}/project_code_root.tar.gz"] = b"code"
    s3.objects[f"job/{job_id}/manifest.json"] = b"{}"
    s3.objects[f"job/{job_id}/lifecycle.json"] = b"{}"


class TestDeleteRun(unittest.TestCase):
    def setUp(self) -> None:
        set_instance(None)
        self.addCleanup(set_instance, None)
        self.records = FakeState().install(self)
        self.mlflow = FakeMlflowClient()

    def test_after_delete_run_is_no_longer_in_listings(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", [])

        with _patched_infra(FakeS3(), self.mlflow, FakeRay()):
            self.assertIn("run-1", [e.run_id for e in list_experiments()])

            delete_run("run-1")

            self.assertNotIn("run-1", [e.run_id for e in list_experiments()])

    def test_mlflow_run_is_soft_deleted(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", [])

        with _patched_infra(FakeS3(), self.mlflow, FakeRay()):
            delete_run("run-1")

        self.assertEqual(self.mlflow.get_run("run-1").lifecycle_stage, "deleted")

    def test_after_delete_no_job_packages_remain_for_runs_jobs(self) -> None:
        _seed_run_with_jobs(
            self.records, self.mlflow, "alpha", "e1", "run-1", ["j1", "j2"]
        )
        s3 = FakeS3()
        _seed_job_package(s3, "j1")
        _seed_job_package(s3, "j2")

        with _patched_infra(s3, self.mlflow, FakeRay()):
            delete_run("run-1")

        self.assertEqual([k for k in s3.objects if k.startswith("job/j1/")], [])
        self.assertEqual([k for k in s3.objects if k.startswith("job/j2/")], [])

    def test_other_runs_job_packages_are_untouched(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", ["j1"])
        _seed_run_with_jobs(self.records, self.mlflow, "beta", "e2", "run-2", ["j2"])
        s3 = FakeS3()
        _seed_job_package(s3, "j1")
        _seed_job_package(s3, "j2")

        with _patched_infra(s3, self.mlflow, FakeRay()):
            delete_run("run-1")

        self.assertIn("job/j2/project_code_root.tar.gz", s3.objects)
        self.assertIn("job/j2/manifest.json", s3.objects)

    def test_running_ray_attempts_for_the_run_are_stopped(self) -> None:
        _seed_run_with_jobs(
            self.records, self.mlflow, "alpha", "e1", "run-1", ["j1", "j2"]
        )
        ray = FakeRay({"run-1-j1-0": "RUNNING", "run-1-j2-0": "RUNNING"})

        with _patched_infra(FakeS3(), self.mlflow, ray):
            delete_run("run-1")

        self.assertEqual(ray.jobs["run-1-j1-0"].status, "STOPPED")
        self.assertEqual(ray.jobs["run-1-j2-0"].status, "STOPPED")

    def test_ray_attempts_for_other_runs_are_untouched(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", ["j1"])
        _seed_run_with_jobs(self.records, self.mlflow, "beta", "e2", "run-2", ["j2"])
        ray = FakeRay({"run-1-j1-0": "RUNNING", "run-2-j2-0": "RUNNING"})

        with _patched_infra(FakeS3(), self.mlflow, ray):
            delete_run("run-1")

        self.assertEqual(ray.jobs["run-2-j2-0"].status, "RUNNING")

class TestDeleteExperiment(unittest.TestCase):
    def setUp(self) -> None:
        set_instance(None)
        self.addCleanup(set_instance, None)
        self.records = FakeState().install(self)
        self.mlflow = FakeMlflowClient()

    def test_after_delete_experiment_no_longer_in_listings(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", [])

        with _patched_infra(FakeS3(), self.mlflow, FakeRay()):
            delete_experiment("alpha")

            self.assertEqual(
                [e.experiment_name for e in list_experiments()], []
            )

    def test_every_run_in_experiment_disappears_from_listings(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", [])
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-2", [])

        with _patched_infra(FakeS3(), self.mlflow, FakeRay()):
            delete_experiment("alpha")

            self.assertEqual(list_experiments(), [])

    def test_no_job_packages_remain_for_any_run_in_experiment(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", ["j1"])
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-2", ["j2"])
        s3 = FakeS3()
        _seed_job_package(s3, "j1")
        _seed_job_package(s3, "j2")

        with _patched_infra(s3, self.mlflow, FakeRay()):
            delete_experiment("alpha")

        self.assertEqual([k for k in s3.objects if k.startswith("job/j1/")], [])
        self.assertEqual([k for k in s3.objects if k.startswith("job/j2/")], [])

    def test_ray_attempts_for_every_run_in_experiment_are_stopped(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", ["j1"])
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-2", ["j2"])
        ray = FakeRay({"run-1-j1-0": "RUNNING", "run-2-j2-0": "RUNNING"})

        with _patched_infra(FakeS3(), self.mlflow, ray):
            delete_experiment("alpha")

        self.assertEqual(ray.jobs["run-1-j1-0"].status, "STOPPED")
        self.assertEqual(ray.jobs["run-2-j2-0"].status, "STOPPED")

    def test_other_experiments_remain_in_listings(self) -> None:
        _seed_run_with_jobs(self.records, self.mlflow, "alpha", "e1", "run-1", [])
        _seed_run_with_jobs(self.records, self.mlflow, "beta", "e2", "run-2", [])

        with _patched_infra(FakeS3(), self.mlflow, FakeRay()):
            delete_experiment("alpha")
            remaining = [e.experiment_name for e in list_experiments()]

        self.assertEqual(remaining, ["beta"])

if __name__ == "__main__":
    unittest.main()
