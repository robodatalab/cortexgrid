from __future__ import annotations

import unittest
from unittest.mock import patch

from cortexgrid.jobs import JobLifecycle
from cortexgrid.ray_util import JobStatus
from cortexgrid_ui.backend.streams.job_status import (
    job_status,
    jobs_of_runs,
    ray_status,
)


def _job(job_id: str = "j1", **kwargs: object) -> JobLifecycle:
    return JobLifecycle(
        experiment_name="alpha", run_id="run-1", job_id=job_id, **kwargs
    )


class TestJobStatus(unittest.TestCase):
    """The one derivation every view of a job goes through."""

    def test_ray_answers_for_a_job_it_knows(self) -> None:
        statuses = {"run-1-j1-0": JobStatus.RUNNING}

        self.assertEqual(job_status(_job(), "run-1-j1-0", statuses), "running")

    def test_a_job_ray_has_never_seen_is_pending(self) -> None:
        self.assertEqual(job_status(_job(), None, {}), "pending")

    def test_a_job_missing_from_the_snapshot_is_broken(self) -> None:
        """It ran once; Ray has since forgotten it, and cannot be asked."""
        self.assertEqual(job_status(_job(), "run-1-j1-0", {}), "broken")

    def test_the_record_overrides_ray_when_the_job_is_going(self) -> None:
        statuses = {"run-1-j1-0": JobStatus.RUNNING}

        self.assertEqual(
            job_status(_job(delete_requested=True), "run-1-j1-0", statuses),
            "deleting",
        )

    def test_without_a_snapshot_ray_is_asked_directly(self) -> None:
        """The single-job views, where one query beats a whole snapshot."""
        with patch(
            "cortexgrid_ui.backend.streams.job_status.get_ray_job_status",
            return_value=JobStatus.FINISHED,
        ) as asked:
            self.assertEqual(ray_status("run-1-j1-0"), "finished")

        asked.assert_called_once_with("run-1-j1-0")

    def test_a_job_ray_chokes_on_costs_only_its_own_row(self) -> None:
        with patch(
            "cortexgrid_ui.backend.streams.job_status.get_ray_job_status",
            side_effect=RuntimeError("ray dashboard is unreachable"),
        ):
            self.assertEqual(ray_status("run-1-j1-0"), "broken")


class TestReadingJobsOfRuns(unittest.TestCase):
    def test_a_run_that_cannot_be_listed_yields_no_jobs(self) -> None:
        def fail_for_run_1(run_id: str) -> list[str]:
            if run_id == "run-1":
                raise RuntimeError("mlflow is having a moment")
            return ["j2"]

        with patch(
            "cortexgrid_ui.backend.streams.job_status.list_experiment_run_job_ids",
            side_effect=fail_for_run_1,
        ):
            with patch(
                "cortexgrid_ui.backend.streams.job_status.load_job",
                side_effect=lambda run_id, job_id: _job(job_id),
            ):
                jobs = jobs_of_runs(["run-1", "run-2"])

        self.assertEqual(jobs["run-1"], [])
        self.assertEqual([j.job_id for j in jobs["run-2"]], ["j2"])

    def test_a_record_that_has_not_landed_yet_is_skipped(self) -> None:
        with patch(
            "cortexgrid_ui.backend.streams.job_status.list_experiment_run_job_ids",
            return_value=["j1", "j2"],
        ):
            with patch(
                "cortexgrid_ui.backend.streams.job_status.load_job",
                side_effect=lambda run_id, job_id: (
                    None if job_id == "j1" else _job(job_id)
                ),
            ):
                jobs = jobs_of_runs(["run-1"])

        self.assertEqual([j.job_id for j in jobs["run-1"]], ["j2"])


if __name__ == "__main__":
    unittest.main()
