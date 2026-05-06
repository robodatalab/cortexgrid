from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from cortexflow.experiment import (
    delete_experiment,
    delete_run,
    list_experiments,
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
        patch("cortexflow.s3_util.get_s3_client", return_value=s3)
    )
    stack.enter_context(
        patch("cortexflow.s3_util.get_s3_bucket", return_value="test-bucket")
    )
    stack.enter_context(
        patch("cortexflow.experiment.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexflow.experiment.get_mlflow_tracking_uri", return_value="")
    )
    stack.enter_context(
        patch("cortexflow.jobs.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexflow.jobs.get_mlflow_tracking_uri", return_value="")
    )
    stack.enter_context(
        patch(
            "cortexflow.ray_util.get_ray_job_submission_client",
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


class TestDeleteRun(unittest.TestCase):
    def setUp(self) -> None:
        set_instance(None)
        self.addCleanup(set_instance, None)

    def test_after_delete_run_is_no_longer_in_listings(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            self.assertIn("run-1", [e.run_id for e in list_experiments()])

            delete_run("run-1")

            self.assertNotIn("run-1", [e.run_id for e in list_experiments()])

    def test_after_delete_no_job_packages_remain_for_runs_jobs(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", ["j1", "j2"]
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)
        s3 = FakeS3()
        _seed_job_package(s3, "j1")
        _seed_job_package(s3, "j2")

        with _patched_infra(s3, mlflow, FakeRay()):
            delete_run("run-1")

        self.assertEqual([k for k in s3.objects if k.startswith("job/j1/")], [])
        self.assertEqual([k for k in s3.objects if k.startswith("job/j2/")], [])

    def test_other_runs_job_packages_are_untouched(self) -> None:
        exp_a, run_a, art_a = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", ["j1"]
        )
        exp_b, run_b, art_b = _experiment_with_run_and_jobs(
            "beta", "e2", "run-2", "beta-run", ["j2"]
        )
        mlflow = FakeMlflowClient().seed(
            [exp_a, exp_b], [run_a, run_b], {**art_a, **art_b}
        )
        s3 = FakeS3()
        _seed_job_package(s3, "j1")
        _seed_job_package(s3, "j2")

        with _patched_infra(s3, mlflow, FakeRay()):
            delete_run("run-1")

        self.assertIn("job/j2/project_code_root.tar.gz", s3.objects)
        self.assertIn("job/j2/manifest.json", s3.objects)

    def test_running_ray_attempts_for_the_run_are_stopped(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", ["j1", "j2"]
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)
        ray = FakeRay({"run-1-j1-0": "RUNNING", "run-1-j2-0": "RUNNING"})

        with _patched_infra(FakeS3(), mlflow, ray):
            delete_run("run-1")

        self.assertEqual(ray.jobs["run-1-j1-0"].status, "STOPPED")
        self.assertEqual(ray.jobs["run-1-j2-0"].status, "STOPPED")

    def test_ray_attempts_for_other_runs_are_untouched(self) -> None:
        exp_a, run_a, art_a = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", ["j1"]
        )
        exp_b, run_b, art_b = _experiment_with_run_and_jobs(
            "beta", "e2", "run-2", "beta-run", ["j2"]
        )
        mlflow = FakeMlflowClient().seed(
            [exp_a, exp_b], [run_a, run_b], {**art_a, **art_b}
        )
        ray = FakeRay({"run-1-j1-0": "RUNNING", "run-2-j2-0": "RUNNING"})

        with _patched_infra(FakeS3(), mlflow, ray):
            delete_run("run-1")

        self.assertEqual(ray.jobs["run-2-j2-0"].status, "RUNNING")

class TestDeleteExperiment(unittest.TestCase):
    def setUp(self) -> None:
        set_instance(None)
        self.addCleanup(set_instance, None)

    def test_after_delete_experiment_no_longer_in_listings(self) -> None:
        exp, run, artifacts = _experiment_with_run_and_jobs(
            "alpha", "e1", "run-1", "alpha-run", []
        )
        mlflow = FakeMlflowClient().seed([exp], [run], artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")

            self.assertEqual(
                [e.experiment_name for e in list_experiments()], []
            )

    def test_every_run_in_experiment_disappears_from_listings(self) -> None:
        exp = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        runs = [
            FakeMlflowRun(run_id="run-1", run_name="alpha-1", experiment_id="e1"),
            FakeMlflowRun(run_id="run-2", run_name="alpha-2", experiment_id="e1"),
        ]
        artifacts = {"run-1": [], "run-2": []}
        mlflow = FakeMlflowClient().seed([exp], runs, artifacts)

        with _patched_infra(FakeS3(), mlflow, FakeRay()):
            delete_experiment("alpha")

            self.assertEqual(list_experiments(), [])

    def test_no_job_packages_remain_for_any_run_in_experiment(self) -> None:
        exp = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        runs = [
            FakeMlflowRun(run_id="run-1", run_name="alpha-1", experiment_id="e1"),
            FakeMlflowRun(run_id="run-2", run_name="alpha-2", experiment_id="e1"),
        ]
        artifacts = {
            "run-1": [FakeArtifact(path="job/j1", is_dir=True)],
            "run-2": [FakeArtifact(path="job/j2", is_dir=True)],
        }
        mlflow = FakeMlflowClient().seed([exp], runs, artifacts)
        s3 = FakeS3()
        _seed_job_package(s3, "j1")
        _seed_job_package(s3, "j2")

        with _patched_infra(s3, mlflow, FakeRay()):
            delete_experiment("alpha")

        self.assertEqual([k for k in s3.objects if k.startswith("job/j1/")], [])
        self.assertEqual([k for k in s3.objects if k.startswith("job/j2/")], [])

    def test_ray_attempts_for_every_run_in_experiment_are_stopped(self) -> None:
        exp = FakeMlflowExperiment(experiment_id="e1", name="alpha")
        runs = [
            FakeMlflowRun(run_id="run-1", run_name="alpha-1", experiment_id="e1"),
            FakeMlflowRun(run_id="run-2", run_name="alpha-2", experiment_id="e1"),
        ]
        artifacts = {
            "run-1": [FakeArtifact(path="job/j1", is_dir=True)],
            "run-2": [FakeArtifact(path="job/j2", is_dir=True)],
        }
        mlflow = FakeMlflowClient().seed([exp], runs, artifacts)
        ray = FakeRay({"run-1-j1-0": "RUNNING", "run-2-j2-0": "RUNNING"})

        with _patched_infra(FakeS3(), mlflow, ray):
            delete_experiment("alpha")

        self.assertEqual(ray.jobs["run-1-j1-0"].status, "STOPPED")
        self.assertEqual(ray.jobs["run-2-j2-0"].status, "STOPPED")

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

if __name__ == "__main__":
    unittest.main()
