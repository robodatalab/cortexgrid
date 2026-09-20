from __future__ import annotations

import unittest
from contextlib import ExitStack, contextmanager
from typing import Iterator
from unittest.mock import patch

from cortexgrid.experiment import Experiment
from cortexgrid.jobs import JobLifecycle

from cortexgrid_ui.backend.streams import jobs_stream


def _experiment(name: str, run_id: str, run_name: str) -> Experiment:
    exp = Experiment(experiment_name=name, run_id=run_id)
    # run_name() is an MLflow round trip; the stream makes one per run.
    exp.run_name = lambda: run_name  # type: ignore[method-assign]
    return exp


def _job(run_id: str, job_id: str, experiment_name: str = "alpha") -> JobLifecycle:
    return JobLifecycle(
        experiment_name=experiment_name, run_id=run_id, job_id=job_id
    )


@contextmanager
def _cluster(
    experiments: list[Experiment],
    jobs_by_run: dict[str, list[JobLifecycle]] | Exception,
    ray_submission_ids: list[str],
    ray_statuses: dict[str, str] | None = None,
) -> Iterator[None]:
    """Stand in for the three sources the stream reads."""

    def list_jobs(run_id: str) -> list[JobLifecycle]:
        if isinstance(jobs_by_run, Exception):
            raise jobs_by_run
        return jobs_by_run.get(run_id, [])

    statuses = ray_statuses or {}
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(jobs_stream, "list_experiments", return_value=experiments)
        )
        stack.enter_context(
            patch.object(jobs_stream, "list_experiment_run_jobs", list_jobs)
        )
        stack.enter_context(
            patch.object(
                jobs_stream,
                "list_ray_jobs_with_submission_id",
                return_value=ray_submission_ids,
            )
        )
        # Patched below get_ray_job_status, so the real status mapping runs.
        stack.enter_context(
            patch(
                "cortexgrid.ray_util.get_ray_status",
                side_effect=lambda sid: statuses.get(sid),
            )
        )
        yield


class TestPollJobs(unittest.TestCase):
    def test_a_row_per_job_with_its_experiment_and_run(self) -> None:
        with _cluster(
            experiments=[_experiment("alpha", "run-1", "alpha-run")],
            jobs_by_run={"run-1": [_job("run-1", "j1")]},
            ray_submission_ids=["run-1-j1-0"],
            ray_statuses={"run-1-j1-0": "RUNNING"},
        ):
            rows = jobs_stream.poll_jobs(None)

        self.assertEqual(list(rows), ["run-1/j1"])
        row = rows["run-1/j1"]
        self.assertEqual(row.job_id, "j1")
        self.assertEqual(row.experiment_name, "alpha")
        self.assertEqual(row.run_id, "run-1")
        self.assertEqual(row.run_name, "alpha-run")
        self.assertEqual(row.status, "running")

    def test_a_ray_submission_with_no_lifecycle_record_is_not_a_job(self) -> None:
        """Ray decorates rows; it never produces one. A submission id that no
        lifecycle record claims is not a job and must not be listed."""
        with _cluster(
            experiments=[_experiment("alpha", "run-1", "alpha-run")],
            jobs_by_run={"run-1": [_job("run-1", "j1")]},
            ray_submission_ids=["run-1-j1-0", "run-9-ghost-0", "run-9-ghost-1"],
            ray_statuses={"run-1-j1-0": "RUNNING"},
        ):
            rows = jobs_stream.poll_jobs(None)

        self.assertEqual(list(rows), ["run-1/j1"])

    def test_every_run_of_every_experiment_contributes_its_jobs(self) -> None:
        with _cluster(
            experiments=[
                _experiment("alpha", "run-1", "alpha-run"),
                _experiment("beta", "run-2", "beta-run"),
            ],
            jobs_by_run={
                "run-1": [_job("run-1", "j1"), _job("run-1", "j2")],
                "run-2": [_job("run-2", "j3", experiment_name="beta")],
            },
            ray_submission_ids=[],
        ):
            rows = jobs_stream.poll_jobs(None)

        self.assertEqual(sorted(rows), ["run-1/j1", "run-1/j2", "run-2/j3"])
        self.assertEqual(rows["run-2/j3"].experiment_name, "beta")
        self.assertEqual(rows["run-2/j3"].run_name, "beta-run")

    def test_two_runs_can_hold_a_job_of_the_same_name(self) -> None:
        with _cluster(
            experiments=[
                _experiment("alpha", "run-1", "alpha-run"),
                _experiment("beta", "run-2", "beta-run"),
            ],
            jobs_by_run={
                "run-1": [_job("run-1", "twin")],
                "run-2": [_job("run-2", "twin", experiment_name="beta")],
            },
            ray_submission_ids=[],
        ):
            rows = jobs_stream.poll_jobs(None)

        self.assertEqual(sorted(rows), ["run-1/twin", "run-2/twin"])

    def test_a_run_without_jobs_contributes_nothing(self) -> None:
        with _cluster(
            experiments=[_experiment("alpha", "run-1", "alpha-run")],
            jobs_by_run={},
            ray_submission_ids=[],
        ):
            self.assertEqual(jobs_stream.poll_jobs(None), {})

    def test_status_comes_from_the_latest_attempt(self) -> None:
        with _cluster(
            experiments=[_experiment("alpha", "run-1", "alpha-run")],
            jobs_by_run={"run-1": [_job("run-1", "j1")]},
            ray_submission_ids=["run-1-j1-0", "run-1-j1-1"],
            ray_statuses={"run-1-j1-0": "FAILED", "run-1-j1-1": "SUCCEEDED"},
        ):
            rows = jobs_stream.poll_jobs(None)

        self.assertEqual(rows["run-1/j1"].status, "finished")

    def test_a_job_ray_has_never_seen_is_pending(self) -> None:
        with _cluster(
            experiments=[_experiment("alpha", "run-1", "alpha-run")],
            jobs_by_run={"run-1": [_job("run-1", "j1")]},
            ray_submission_ids=[],
        ):
            rows = jobs_stream.poll_jobs(None)

        self.assertEqual(rows["run-1/j1"].status, "pending")

    def test_a_job_ray_cannot_speak_for_is_broken(self) -> None:
        with _cluster(
            experiments=[_experiment("alpha", "run-1", "alpha-run")],
            jobs_by_run={"run-1": [_job("run-1", "j1")]},
            ray_submission_ids=["run-1-j1-0"],
        ):
            with patch(
                "cortexgrid.ray_util.get_ray_status",
                side_effect=RuntimeError("ray is down"),
            ):
                rows = jobs_stream.poll_jobs(None)

        self.assertEqual(rows["run-1/j1"].status, "broken")

    def test_a_run_that_cannot_be_read_does_not_lose_the_others(self) -> None:
        def list_jobs(run_id: str) -> list[JobLifecycle]:
            if run_id == "run-1":
                raise RuntimeError("artifact store unavailable")
            return [_job("run-2", "j3", experiment_name="beta")]

        with _cluster(
            experiments=[
                _experiment("alpha", "run-1", "alpha-run"),
                _experiment("beta", "run-2", "beta-run"),
            ],
            jobs_by_run={},
            ray_submission_ids=[],
        ):
            with patch.object(
                jobs_stream, "list_experiment_run_jobs", list_jobs
            ):
                rows = jobs_stream.poll_jobs(None)

        self.assertEqual(list(rows), ["run-2/j3"])


if __name__ == "__main__":
    unittest.main()
