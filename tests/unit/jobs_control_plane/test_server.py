from __future__ import annotations

import copy
import os
import shutil
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import requests  # type: ignore
from parameterized import parameterized

from cortexgrid.jobs import JobLifecycle, LifecycleEvent, Payload
from cortexgrid.ray_util import JobStatus, ray_submission_id
from jobs_control_plane.server import (
    _match_ray_jobs_to_cortexgrid_jobs,
    _record_state,
    _submit_job_worker,
    poll_once,
)

from tests.fakes import FakeState


EXPERIMENT_NAME = "exp"
RUN_ID = "run-1"
JOB_ID = "job-1"


def _make_lifecycle(
    run_id: str = RUN_ID,
    job_id: str = JOB_ID,
    stop_requested: bool = False,
    retry: bool = False,
    pip_requirements: list[str] | None = None,
) -> JobLifecycle:
    return JobLifecycle(
        experiment_name=EXPERIMENT_NAME,
        run_id=run_id,
        job_id=job_id,
        stop_requested=stop_requested,
        retry=retry,
        pip_requirements=pip_requirements or [],
    )


class FakeS3:
    """Fake s3_util backed by a temp directory."""

    BUCKET = "canonical"

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def upload(self, local_path: str, dest_path: str | None = None) -> str:
        dest_path = dest_path or Path(local_path).name
        dest = self.root / self.BUCKET / dest_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)
        return f"s3://{self.BUCKET}/{dest_path}"

    def download(self, src_path: str, local_path: str | None = None) -> str:
        local_path = local_path or Path(src_path).name
        shutil.copy2(self.root / self.BUCKET / src_path, local_path)
        return local_path


class TestMatchRayJobsToCortexgridJobs(unittest.TestCase):
    """Exhaustive tests for ``_match_ray_jobs_to_cortexgrid_jobs``.

    The match function takes the current set of cortexgrid jobs and the
    current set of Ray submission ids (queried internally) and returns a
    list of ``(cjob, latest_ray_submission_id_or_None)`` pairs.
    """

    @parameterized.expand(
        [
            (
                "no_ray_jobs",
                [("run-1", "job-1")],
                [],
                [("run-1", "job-1", None)],
            ),
            (
                "single_attempt",
                [("run-1", "job-1")],
                ["run-1-job-1-0"],
                [("run-1", "job-1", "run-1-job-1-0")],
            ),
            (
                "multiple_attempts_picks_numeric_max",
                [("run-1", "job-1")],
                ["run-1-job-1-0", "run-1-job-1-1", "run-1-job-1-2"],
                [("run-1", "job-1", "run-1-job-1-2")],
            ),
            (
                "attempts_past_nine_sorted_numerically",
                [("run-1", "job-1")],
                [
                    "run-1-job-1-0",
                    "run-1-job-1-9",
                    "run-1-job-1-10",
                    "run-1-job-1-11",
                    "run-1-job-1-2",
                ],
                [("run-1", "job-1", "run-1-job-1-11")],
            ),
            (
                "prefix_overlap_does_not_cross_attribute",
                [("run-1", "job-1"), ("run-1", "job-10")],
                ["run-1-job-1-0", "run-1-job-10-0", "run-1-job-10-1"],
                [
                    ("run-1", "job-1", "run-1-job-1-0"),
                    ("run-1", "job-10", "run-1-job-10-1"),
                ],
            ),
            (
                "legacy_format_without_attempt_suffix_ignored",
                [("run-1", "job-1")],
                ["run-1-job-1"],
                [("run-1", "job-1", None)],
            ),
            (
                "unrelated_ray_jobs_ignored",
                [("run-1", "job-1")],
                ["run-2-job-2-0", "garbage", "run-1-job-2-0"],
                [("run-1", "job-1", None)],
            ),
            (
                "multiple_cjobs_distinct_attempts",
                [("run-1", "job-a"), ("run-1", "job-b")],
                ["run-1-job-a-0", "run-1-job-b-0", "run-1-job-b-1"],
                [
                    ("run-1", "job-a", "run-1-job-a-0"),
                    ("run-1", "job-b", "run-1-job-b-1"),
                ],
            ),
            (
                "mixed_cjobs_some_without_ray_jobs",
                [("run-1", "job-a"), ("run-1", "job-b")],
                ["run-1-job-b-0"],
                [
                    ("run-1", "job-a", None),
                    ("run-1", "job-b", "run-1-job-b-0"),
                ],
            ),
            (
                "empty_cjobs_returns_empty",
                [],
                ["run-1-job-1-0"],
                [],
            ),
        ]
    )
    def test_match(
        self,
        name: str,
        cjobs: list[tuple[str, str]],
        ray_submission_ids: list[str],
        expected: list[tuple[str, str, str | None]],
    ) -> None:
        lifecycles = [_make_lifecycle(run_id=r, job_id=j) for r, j in cjobs]
        with patch(
            "jobs_control_plane.server.list_ray_jobs_with_submission_id",
            return_value=ray_submission_ids,
        ):
            pairs = _match_ray_jobs_to_cortexgrid_jobs(lifecycles)
        actual = [(lc.run_id, lc.job_id, sid) for lc, sid in pairs]
        self.assertEqual(actual, expected)


class TestPollOnce(unittest.TestCase):
    """Tests for ``poll_once`` covering the cjob × ray_state action matrix.

    The jobs live in a ``FakeState`` holding a single run; the test rig
    patches the Ray calls ``poll_once`` makes (``list_ray_jobs_with_submission_id``,
    ``get_ray_job_status``), the one outbound side effect (``stop_ray_job``)
    and the Serve controller read behind ``observe_deployments``. The
    executor is a MagicMock whose ``submit`` records ``(run_id, job_id, attempt)``.
    """

    def setUp(self) -> None:
        self.records = FakeState().install(self)
        self.records.seed_run(RUN_ID, experiment_name=EXPERIMENT_NAME)
        self._serve_details = MagicMock(return_value={})
        self._ray_state: dict[str, JobStatus] = {}
        self._submitted: list[tuple[str, str, int]] = []
        self._stopped: list[str] = []
        self.in_flight: dict[str, tuple[str, str, Future]] = {}

        self.executor = MagicMock()

        def _submit_side_effect(
            fn: Any, run_id: str, job_id: str, attempt: int
        ) -> Future:
            self._submitted.append((run_id, job_id, attempt))
            return Future()

        self.executor.submit.side_effect = _submit_side_effect

        def _get_status(rjob: str | None) -> JobStatus:
            if rjob is None:
                return JobStatus.PENDING
            return self._ray_state[rjob]

        patchers = [
            patch(
                "jobs_control_plane.server.list_ray_jobs_with_submission_id",
                side_effect=lambda: list(self._ray_state.keys()),
            ),
            patch(
                "jobs_control_plane.server.get_ray_job_status",
                side_effect=_get_status,
            ),
            patch(
                "jobs_control_plane.server.stop_ray_job",
                side_effect=self._stopped.append,
            ),
            patch("cortexgrid.model_serving.status.get_serve_details", self._serve_details),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _seed_ray_attempt(
        self, run_id: str, job_id: str, attempt: int, status: JobStatus
    ) -> None:
        self._ray_state[ray_submission_id(run_id, job_id, attempt)] = status

    @parameterized.expand(
        [
            # ---- unsubmitted (rjob is None) ---------------------------
            ("unsubmitted__no_retry__no_stop", None, False, False, "dispatch_0"),
            ("unsubmitted__retry__no_stop", None, True, False, "dispatch_0"),
            ("unsubmitted__no_retry__stop_requested", None, False, True, "noop"),
            ("unsubmitted__retry__stop_requested", None, True, True, "noop"),
            # ---- Ray says RUNNING -------------------------------------
            ("running__no_retry__no_stop", JobStatus.RUNNING, False, False, "noop"),
            ("running__retry__no_stop", JobStatus.RUNNING, True, False, "noop"),
            ("running__stop_requested", JobStatus.RUNNING, False, True, "stop_0"),
            # ---- Ray says FINISHED ------------------------------------
            ("finished__no_retry__no_stop", JobStatus.FINISHED, False, False, "noop"),
            ("finished__retry__no_stop", JobStatus.FINISHED, True, False, "noop"),
            ("finished__stop_requested", JobStatus.FINISHED, False, True, "stop_0"),
            # ---- Ray says FAILED --------------------------------------
            ("failed__no_retry__no_stop", JobStatus.FAILED, False, False, "noop"),
            ("failed__retry__no_stop", JobStatus.FAILED, True, False, "retry_1"),
            # Bug #5 guard — stop_requested must suppress retry, not race it.
            ("failed__retry__stop_requested", JobStatus.FAILED, True, True, "stop_0"),
            (
                "failed__no_retry__stop_requested",
                JobStatus.FAILED,
                False,
                True,
                "stop_0",
            ),
            # ---- Ray says STOPPED -------------------------------------
            (
                "stopped_in_ray__no_retry__no_stop",
                JobStatus.STOPPED,
                False,
                False,
                "noop",
            ),
            ("stopped_in_ray__retry__no_stop", JobStatus.STOPPED, True, False, "noop"),
            (
                "stopped_in_ray__stop_requested",
                JobStatus.STOPPED,
                False,
                True,
                "stop_0",
            ),
        ]
    )
    def test_action_matrix(
        self,
        name: str,
        ray_state: JobStatus | None,
        retry: bool,
        stop_requested: bool,
        expected_action: str,
    ) -> None:
        self.records.seed_job(
            _make_lifecycle(retry=retry, stop_requested=stop_requested)
        )
        if ray_state is not None:
            self._seed_ray_attempt(RUN_ID, JOB_ID, 0, ray_state)

        poll_once(self.executor, self.in_flight)

        sid_0 = ray_submission_id(RUN_ID, JOB_ID, 0)
        if expected_action == "dispatch_0":
            self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 0)])
            self.assertEqual(self._stopped, [])
        elif expected_action == "retry_1":
            self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 1)])
            self.assertEqual(self._stopped, [])
        elif expected_action == "stop_0":
            self.assertEqual(self._submitted, [])
            self.assertEqual(self._stopped, [sid_0])
        elif expected_action == "noop":
            self.assertEqual(self._submitted, [])
            self.assertEqual(self._stopped, [])
        else:
            self.fail(f"unknown expected_action {expected_action!r}")

    def test_retry_uses_next_attempt_number_after_several_failures(self) -> None:
        """A retry chain must increment the attempt beyond the highest Ray knows."""
        self.records.seed_job(_make_lifecycle(retry=True))
        for attempt in range(4):
            self._seed_ray_attempt(RUN_ID, JOB_ID, attempt, JobStatus.FAILED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 4)])

    def test_retry_picks_latest_attempt_even_if_older_attempts_also_failed(
        self,
    ) -> None:
        """Regression for the lex-sort bug: attempt 10 must win over attempt 2."""
        self.records.seed_job(_make_lifecycle(retry=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FAILED)
        self._seed_ray_attempt(RUN_ID, JOB_ID, 2, JobStatus.FAILED)
        self._seed_ray_attempt(RUN_ID, JOB_ID, 10, JobStatus.FAILED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 11)])

    def test_in_flight_job_is_not_redispatched(self) -> None:
        self.records.seed_job(_make_lifecycle())
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        self.in_flight[key] = (RUN_ID, JOB_ID, Future())  # not done

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [])

    def test_in_flight_job_with_failed_ray_state_is_not_redispatched(self) -> None:
        """If a worker is still running for this job identity, skip it
        regardless of what Ray reports for the most recent attempt."""
        self.records.seed_job(_make_lifecycle(retry=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FAILED)
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        self.in_flight[key] = (RUN_ID, JOB_ID, Future())  # not done

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [])

    def test_done_futures_are_reaped_before_dispatch(self) -> None:
        self.records.seed_job(_make_lifecycle())
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        done: Future = Future()
        done.set_result(None)
        self.in_flight[key] = (RUN_ID, JOB_ID, done)

        updated_in_flight = poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 0)])
        self.assertIsNot(updated_in_flight[key][2], done)

    def test_multiple_jobs_are_handled_independently(self) -> None:
        for lifecycle in [
            _make_lifecycle(job_id="job-new"),
            _make_lifecycle(job_id="job-running"),
            _make_lifecycle(job_id="job-fail", retry=True),
            _make_lifecycle(job_id="job-stop", stop_requested=True),
            _make_lifecycle(job_id="job-done"),
        ]:
            self.records.seed_job(lifecycle)
        self._seed_ray_attempt(RUN_ID, "job-running", 0, JobStatus.RUNNING)
        self._seed_ray_attempt(RUN_ID, "job-fail", 0, JobStatus.FAILED)
        self._seed_ray_attempt(RUN_ID, "job-stop", 0, JobStatus.RUNNING)
        self._seed_ray_attempt(RUN_ID, "job-done", 0, JobStatus.FINISHED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(
            sorted(self._submitted),
            sorted([(RUN_ID, "job-new", 0), (RUN_ID, "job-fail", 1)]),
        )
        self.assertEqual(self._stopped, [ray_submission_id(RUN_ID, "job-stop", 0)])

    def test_in_flight_is_populated_after_dispatch(self) -> None:
        self.records.seed_job(_make_lifecycle())

        updated_in_flight = poll_once(self.executor, self.in_flight)

        self.assertIn(ray_submission_id(RUN_ID, JOB_ID, None), updated_in_flight)

    def test_worker_exception_is_persisted_to_last_event_error(self) -> None:
        """A worker that raised has its error attached to the last history event."""
        lifecycle = _make_lifecycle()
        lifecycle.history.append(
            LifecycleEvent(
                attempt=0, state="pending", start="2026-04-15T10:00:00+00:00"
            )
        )
        self.records.seed_job(lifecycle)
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        failed: Future = Future()
        failed.set_exception(RuntimeError("payload download failed"))
        self.in_flight[key] = (RUN_ID, JOB_ID, failed)

        updated_in_flight = poll_once(self.executor, self.in_flight)

        history = self.records.jobs[(RUN_ID, JOB_ID)]["history"]
        self.assertEqual(history[-1]["error"], "payload download failed")
        # The job is still pending, so the same cycle hands it to a fresh worker.
        self.assertIsNot(updated_in_flight[key][2], failed)

    def test_worker_exception_does_not_block_subsequent_dispatch(self) -> None:
        """After a worker raises, the next poll can redispatch the job."""
        self.records.seed_job(_make_lifecycle())
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        failed: Future = Future()
        failed.set_exception(RuntimeError("boom"))
        self.in_flight[key] = (RUN_ID, JOB_ID, failed)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 0)])

    @parameterized.expand(
        [
            ("finished", JobStatus.FINISHED, False),
            ("stopped_even_with_retry", JobStatus.STOPPED, True),
            ("failed_without_retry", JobStatus.FAILED, False),
        ]
    )
    def test_job_done_for_good_is_not_read_by_later_cycles(
        self, name: str, ray_state: JobStatus, retry: bool
    ) -> None:
        """Once a cycle records a terminal state with no retry to follow, the
        job leaves jobs/open and later cycles leave it alone."""
        self.records.seed_job(_make_lifecycle(retry=retry))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, ray_state)
        poll_once(self.executor, self.in_flight)
        recorded = copy.deepcopy(self.records.jobs[(RUN_ID, JOB_ID)])
        # Ray forgetting the job (a head restart) would make a cycle that
        # still read it record a fresh pending entry.
        self._ray_state.clear()

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self.records.jobs[(RUN_ID, JOB_ID)], recorded)
        self.assertEqual(self._submitted, [])

    def test_failing_deployment_observation_does_not_fail_the_cycle(self) -> None:
        """A Serve controller that cannot be read must not hold up the jobs."""
        self.records.seed_job(_make_lifecycle())
        self._serve_details.side_effect = requests.ConnectionError("serve is down")

        with self.assertLogs("jobs-control-plane", level="ERROR"):
            updated_in_flight = poll_once(self.executor, self.in_flight)

        self._serve_details.assert_called_once()
        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 0)])
        self.assertIn(ray_submission_id(RUN_ID, JOB_ID, None), updated_in_flight)


class TestSubmitJobWorker(unittest.TestCase):
    """Sanity tests for ``_submit_job_worker`` under its new contract.

    The worker now always appends an ``attempt`` suffix to the submission
    id, raises on state/S3/Ray errors (no more swallowing), and does not
    write anything back to the lifecycle.
    """

    def setUp(self) -> None:
        self.records = FakeState().install(self)
        self.records.seed_run(RUN_ID, experiment_name=EXPERIMENT_NAME)
        self.fake_s3 = FakeS3()
        self.submitted: list[dict[str, Any]] = []
        self.project_root = Path(tempfile.mkdtemp())
        (self.project_root / "pyproject.toml").write_text("[project]\nname='t'\n")
        self.original_cwd = os.getcwd()
        os.chdir(self.project_root)

        def _submit_ray_job(**kwargs: Any) -> None:
            self.submitted.append(kwargs)

        patchers = [
            patch("cortexgrid.jobs.s3_util", self.fake_s3),
            patch(
                "jobs_control_plane.server.submit_ray_job",
                side_effect=_submit_ray_job,
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(os.chdir, self.original_cwd)
        self.addCleanup(shutil.rmtree, self.project_root, True)
        self.addCleanup(shutil.rmtree, self.fake_s3.root, True)

    def _seed(
        self,
        stop_requested: bool = False,
        with_payload: bool = True,
        pip_requirements: list[str] | None = None,
    ) -> None:
        self.records.seed_job(
            _make_lifecycle(
                stop_requested=stop_requested, pip_requirements=pip_requirements
            )
        )
        if with_payload:
            # Uploads the code tarball to the fake S3 and records its manifest.
            Payload(
                experiment_name=EXPERIMENT_NAME,
                run_id=RUN_ID,
                job_id=JOB_ID,
                fn=_noop,
                args=(),
                kwargs={},
                project_code_root=str(self.project_root),
            ).save_to_mlflow()

    def test_happy_path_submits_to_ray_with_attempt_suffix(self) -> None:
        self._seed()

        _submit_job_worker(RUN_ID, JOB_ID, 0)

        self.assertEqual(len(self.submitted), 1)
        self.assertEqual(
            self.submitted[0]["submission_id"],
            ray_submission_id(RUN_ID, JOB_ID, 0),
        )

    def test_retry_attempt_uses_a_fresh_submission_id(self) -> None:
        self._seed()

        _submit_job_worker(RUN_ID, JOB_ID, 3)

        self.assertEqual(
            self.submitted[0]["submission_id"],
            ray_submission_id(RUN_ID, JOB_ID, 3),
        )

    def test_pip_requirements_are_installed_through_the_runtime_env(self) -> None:
        self._seed(pip_requirements=["haikunator==2.1.0", "tqdm==4.67.3"])

        _submit_job_worker(RUN_ID, JOB_ID, 0)

        runtime_env = self.submitted[0]["runtime_env"]
        self.assertIn("working_dir", runtime_env)
        self.assertEqual(runtime_env["pip"], ["haikunator==2.1.0", "tqdm==4.67.3"])

    def test_no_pip_key_when_there_are_no_requirements(self) -> None:
        # A pip key, even an empty one, makes Ray build a virtualenv.
        self._seed()

        _submit_job_worker(RUN_ID, JOB_ID, 0)

        self.assertEqual(set(self.submitted[0]["runtime_env"]), {"working_dir"})

    def test_stop_requested_short_circuits_before_submitting(self) -> None:
        self._seed(stop_requested=True)

        _submit_job_worker(RUN_ID, JOB_ID, 0)

        self.assertEqual(self.submitted, [])

    def test_missing_payload_propagates_the_error(self) -> None:
        self._seed(with_payload=False)

        with self.assertRaises(Exception):
            _submit_job_worker(RUN_ID, JOB_ID, 0)

        self.assertEqual(
            self.submitted,
            [
                {
                    "submission_id": ray_submission_id(RUN_ID, JOB_ID, 0),
                    "entrypoint": "exit 1",
                    "runtime_env": {},
                }
            ],
        )


def _noop() -> None:
    pass


class TestRecordState(unittest.TestCase):
    """Tests for ``_record_state`` — observation-to-history recorder."""

    def setUp(self) -> None:
        patchers = [
            patch("cortexgrid.jobs.JobLifecycle.save_to_mlflow"),
            patch("jobs_control_plane.server.get_ray_job_status"),
            patch("jobs_control_plane.server.get_ray_job_attempt"),
        ]
        self._save, self._status, self._attempt = [p.start() for p in patchers]
        for p in patchers:
            self.addCleanup(p.stop)

    def _observe(self, cjob: JobLifecycle, attempt: int, state: JobStatus) -> None:
        self._attempt.return_value = attempt
        self._status.return_value = state
        _record_state(cjob, f"{cjob.run_id}-{cjob.job_id}-{attempt}")

    def test_first_observation_appends_open_entry(self) -> None:
        cjob = _make_lifecycle()
        self._observe(cjob, 0, JobStatus.PENDING)
        self.assertEqual(len(cjob.history), 1)
        self.assertEqual(cjob.history[0].attempt, 0)
        self.assertEqual(cjob.history[0].state, JobStatus.PENDING.value)
        self.assertIsNone(cjob.history[0].end)
        self.assertEqual(self._save.call_count, 1)

    def test_same_state_is_noop(self) -> None:
        cjob = _make_lifecycle()
        self._observe(cjob, 0, JobStatus.RUNNING)
        self._observe(cjob, 0, JobStatus.RUNNING)
        self.assertEqual(len(cjob.history), 1)
        self.assertEqual(self._save.call_count, 1)

    def test_state_change_closes_and_appends(self) -> None:
        cjob = _make_lifecycle()
        self._observe(cjob, 0, JobStatus.PENDING)
        self._observe(cjob, 0, JobStatus.RUNNING)
        self.assertEqual(len(cjob.history), 2)
        self.assertIsNotNone(cjob.history[0].end)
        self.assertEqual(cjob.history[0].state, JobStatus.PENDING.value)
        self.assertEqual(cjob.history[1].state, JobStatus.RUNNING.value)
        self.assertIsNone(cjob.history[1].end)
        self.assertEqual(self._save.call_count, 2)

    def test_attempt_change_closes_and_appends(self) -> None:
        cjob = _make_lifecycle()
        self._observe(cjob, 0, JobStatus.FAILED)
        self._observe(cjob, 1, JobStatus.PENDING)
        self.assertEqual(len(cjob.history), 2)
        self.assertEqual(cjob.history[0].attempt, 0)
        self.assertIsNotNone(cjob.history[0].end)
        self.assertEqual(cjob.history[1].attempt, 1)
        self.assertIsNone(cjob.history[1].end)
        self.assertEqual(self._save.call_count, 2)

    def test_closed_entry_end_equals_next_start(self) -> None:
        """On a transition the prior entry's end should match the new entry's start."""
        cjob = _make_lifecycle()
        self._observe(cjob, 0, JobStatus.PENDING)
        self._observe(cjob, 0, JobStatus.RUNNING)
        self.assertEqual(cjob.history[0].end, cjob.history[1].start)

    def test_event_captures_ray_job_id_at_record_time(self) -> None:
        """The ray submission id observed at record time is stored on the event."""
        cjob = _make_lifecycle()
        self._attempt.return_value = 0
        self._status.return_value = JobStatus.PENDING
        _record_state(cjob, None)
        self._attempt.return_value = 0
        self._status.return_value = JobStatus.RUNNING
        _record_state(cjob, "run-1-job-1-0")
        self.assertIsNone(cjob.history[0].ray_job_id)
        self.assertEqual(cjob.history[1].ray_job_id, "run-1-job-1-0")


if __name__ == "__main__":
    unittest.main()
