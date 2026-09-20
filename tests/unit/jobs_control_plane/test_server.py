from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import cloudpickle  # type: ignore
from parameterized import parameterized

from cortexgrid.experiment import Experiment
from cortexgrid.jobs import JobLifecycle, LifecycleEvent, Payload
from cortexgrid.ray_util import JobStatus, ray_submission_id
from jobs_control_plane.server import (
    _match_ray_jobs_to_cortexgrid_jobs,
    _record_state,
    _submit_job_worker,
    poll_once,
)


EXPERIMENT_NAME = "exp"
RUN_ID = "run-1"
JOB_ID = "job-1"


def _make_lifecycle(
    run_id: str = RUN_ID,
    job_id: str = JOB_ID,
    stop_requested: bool = False,
    retry: bool = False,
    pip_requirements: list[str] | None = None,
    delete_requested: bool = False,
) -> JobLifecycle:
    return JobLifecycle(
        experiment_name=EXPERIMENT_NAME,
        run_id=run_id,
        job_id=job_id,
        stop_requested=stop_requested,
        retry=retry,
        pip_requirements=pip_requirements or [],
        delete_requested=delete_requested,
    )


class FakeMLflow:
    """MlflowClient stand-in backed by a real temp directory."""

    def __init__(self) -> None:
        self.artifact_root = Path(tempfile.mkdtemp())

    def list_artifacts(self, run_id: str, path: str = "") -> list[SimpleNamespace]:
        target = self.artifact_root / path
        if not target.exists():
            return []
        return [
            SimpleNamespace(path=f"{path}/{d.name}", is_dir=d.is_dir())
            for d in target.iterdir()
        ]

    def download_artifacts(self, run_id: str, artifact_path: str) -> str:
        target = self.artifact_root / artifact_path
        if not target.exists():
            raise FileNotFoundError(artifact_path)
        return str(target)

    def log_artifact(
        self, run_id: str, local_path: str, artifact_path: str = ""
    ) -> None:
        dest_dir = self.artifact_root / artifact_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest_dir / Path(local_path).name)

    def log_artifacts(
        self, run_id: str, local_dir: str, artifact_path: str = ""
    ) -> None:
        dest = self.artifact_root / artifact_path
        shutil.copytree(local_dir, str(dest), dirs_exist_ok=True)

    def add_job(
        self,
        job_id: str,
        lifecycle: JobLifecycle,
        payload: Payload | None = None,
        fake_s3: "FakeS3 | None" = None,
    ) -> None:
        job_dir = self.artifact_root / "job" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "lifecycle.json").write_text(lifecycle.to_json())
        if payload is not None:
            assert fake_s3 is not None, "fake_s3 is required when seeding a payload"
            staging = Path(tempfile.mkdtemp())
            project_dest = staging / "project_code_root"
            shutil.copytree(payload.project_code_root, str(project_dest), dirs_exist_ok=True)
            (project_dest / "payload.pkl").write_bytes(cloudpickle.dumps(payload))
            tarball = staging / "project_code_root.tar.gz"
            with tarfile.open(tarball, "w:gz") as tar:
                tar.add(str(project_dest), arcname="project_code_root")
            uri = fake_s3.upload(
                str(tarball), dest_path=f"job/{job_id}/project_code_root.tar.gz"
            )
            (job_dir / "manifest.json").write_text(json.dumps({"code_tarball_uri": uri}))


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

    The test rig patches the four boundary calls ``poll_once`` makes into
    cortexgrid (``list_experiments``, ``list_experiment_run_jobs``,
    ``list_ray_jobs_with_submission_id``, ``get_ray_job_status``) and the
    one outbound side effect (``stop_ray_job``). The executor is a
    MagicMock whose ``submit`` records ``(run_id, job_id, attempt)``.
    """

    def setUp(self) -> None:
        self._cjobs: list[JobLifecycle] = []
        self._ray_state: dict[str, JobStatus] = {}
        self._submitted: list[tuple[str, str, int]] = []
        self._stopped: list[str] = []
        self._ray_deleted: list[str] = []
        self._s3_deleted: list[str] = []
        self._artifacts_deleted: list[tuple[str, str]] = []
        self._recorded: list[JobLifecycle] = []
        self.in_flight: dict[str, tuple[str, str, Future]] = {}

        self.executor = MagicMock()

        def _submit_side_effect(
            fn: Any, run_id: str, job_id: str, attempt: int
        ) -> Future:
            self._submitted.append((run_id, job_id, attempt))
            return Future()

        self.executor.submit.side_effect = _submit_side_effect

        def _list_jobs(run_id: str) -> list[JobLifecycle]:
            return [j for j in self._cjobs if j.run_id == run_id]

        def _get_status(rjob: str | None) -> JobStatus:
            if rjob is None:
                return JobStatus.PENDING
            return self._ray_state[rjob]

        patchers = [
            patch(
                "jobs_control_plane.server.list_experiments",
                side_effect=lambda: [
                    Experiment(experiment_name=EXPERIMENT_NAME, run_id=r)
                    for r in sorted({j.run_id for j in self._cjobs})
                ],
            ),
            patch(
                "jobs_control_plane.server.list_experiment_run_jobs",
                side_effect=_list_jobs,
            ),
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
            patch(
                "jobs_control_plane.server.delete_ray_job",
                side_effect=self._ray_deleted.append,
            ),
            patch(
                "jobs_control_plane.server.delete_prefix",
                side_effect=self._s3_deleted.append,
            ),
            patch(
                "jobs_control_plane.server.delete_run_artifacts",
                side_effect=lambda run_id, path: self._artifacts_deleted.append(
                    (run_id, path)
                ),
            ),
            # A spy that still calls through: recording must keep working.
            patch(
                "jobs_control_plane.server._record_state",
                side_effect=lambda cjob, rjob: (
                    self._recorded.append(cjob),
                    _record_state(cjob, rjob),
                )[1],
            ),
            patch("cortexgrid.jobs.JobLifecycle.save_to_mlflow"),
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
        self._cjobs.append(_make_lifecycle(retry=retry, stop_requested=stop_requested))
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

    def _erased(self) -> tuple[list[str], list[tuple[str, str]]]:
        return self._s3_deleted, self._artifacts_deleted

    def test_marked_job_with_a_live_attempt_is_only_stopped(self) -> None:
        """Ray will not release a job that is still running, so teardown waits."""
        self._cjobs.append(_make_lifecycle(delete_requested=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.RUNNING)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._stopped, [ray_submission_id(RUN_ID, JOB_ID, 0)])
        self.assertEqual(self._ray_deleted, [])
        self.assertEqual(self._erased(), ([], []))

    def test_marked_job_is_erased_once_its_attempts_have_settled(self) -> None:
        self._cjobs.append(_make_lifecycle(delete_requested=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FINISHED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._stopped, [])
        self.assertEqual(self._ray_deleted, [ray_submission_id(RUN_ID, JOB_ID, 0)])
        self.assertEqual(self._s3_deleted, [f"job/{JOB_ID}/"])
        self.assertEqual(self._artifacts_deleted, [(RUN_ID, f"job/{JOB_ID}")])

    def test_stopped_attempt_is_erased_on_the_following_cycle(self) -> None:
        """Cycle one stops it, cycle two finds it settled and finishes the job."""
        self._cjobs.append(_make_lifecycle(delete_requested=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.RUNNING)

        poll_once(self.executor, self.in_flight)
        self._ray_state[ray_submission_id(RUN_ID, JOB_ID, 0)] = JobStatus.STOPPED
        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._ray_deleted, [ray_submission_id(RUN_ID, JOB_ID, 0)])
        self.assertEqual(self._erased(), ([f"job/{JOB_ID}/"], [(RUN_ID, f"job/{JOB_ID}")]))

    def test_job_created_and_marked_before_it_ever_ran_is_simply_erased(self) -> None:
        """The create/delete pair collapses: nothing is submitted, ever."""
        self._cjobs.append(_make_lifecycle(delete_requested=True))

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [])
        self.assertEqual(self._stopped, [])
        self.assertEqual(self._ray_deleted, [])
        self.assertEqual(self._erased(), ([f"job/{JOB_ID}/"], [(RUN_ID, f"job/{JOB_ID}")]))

    def test_marked_job_is_not_retried_even_when_it_failed(self) -> None:
        self._cjobs.append(_make_lifecycle(retry=True, delete_requested=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FAILED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [])

    def test_marked_job_records_no_further_history(self) -> None:
        """A job on its way out gets no new lifecycle events."""
        self._cjobs.append(_make_lifecycle(delete_requested=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FINISHED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._recorded, [])

    def test_every_attempt_is_erased_not_only_the_latest(self) -> None:
        self._cjobs.append(_make_lifecycle(delete_requested=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FAILED)
        self._seed_ray_attempt(RUN_ID, JOB_ID, 1, JobStatus.FINISHED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(
            sorted(self._ray_deleted),
            [
                ray_submission_id(RUN_ID, JOB_ID, 0),
                ray_submission_id(RUN_ID, JOB_ID, 1),
            ],
        )

    def test_attempts_of_other_jobs_are_left_alone(self) -> None:
        self._cjobs.append(_make_lifecycle(delete_requested=True))
        self._cjobs.append(_make_lifecycle(job_id="job-2"))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FINISHED)
        self._seed_ray_attempt(RUN_ID, "job-2", 0, JobStatus.RUNNING)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._ray_deleted, [ray_submission_id(RUN_ID, JOB_ID, 0)])
        self.assertEqual(self._s3_deleted, [f"job/{JOB_ID}/"])

    def test_a_job_being_torn_down_does_not_hold_up_the_others(self) -> None:
        self._cjobs.append(_make_lifecycle(delete_requested=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.RUNNING)
        self._cjobs.append(_make_lifecycle(job_id="job-2"))

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, "job-2", 0)])

    def test_retry_uses_next_attempt_number_after_several_failures(self) -> None:
        """A retry chain must increment the attempt beyond the highest Ray knows."""
        self._cjobs.append(_make_lifecycle(retry=True))
        for attempt in range(4):
            self._seed_ray_attempt(RUN_ID, JOB_ID, attempt, JobStatus.FAILED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 4)])

    def test_retry_picks_latest_attempt_even_if_older_attempts_also_failed(
        self,
    ) -> None:
        """Regression for the lex-sort bug: attempt 10 must win over attempt 2."""
        self._cjobs.append(_make_lifecycle(retry=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FAILED)
        self._seed_ray_attempt(RUN_ID, JOB_ID, 2, JobStatus.FAILED)
        self._seed_ray_attempt(RUN_ID, JOB_ID, 10, JobStatus.FAILED)

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 11)])

    def test_in_flight_job_is_not_redispatched(self) -> None:
        self._cjobs.append(_make_lifecycle())
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        self.in_flight[key] = (RUN_ID, JOB_ID, Future())  # not done

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [])

    def test_in_flight_job_with_failed_ray_state_is_not_redispatched(self) -> None:
        """If a worker is still running for this job identity, skip it
        regardless of what Ray reports for the most recent attempt."""
        self._cjobs.append(_make_lifecycle(retry=True))
        self._seed_ray_attempt(RUN_ID, JOB_ID, 0, JobStatus.FAILED)
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        self.in_flight[key] = (RUN_ID, JOB_ID, Future())  # not done

        poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [])

    def test_done_futures_are_reaped_before_dispatch(self) -> None:
        self._cjobs.append(_make_lifecycle())
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        done: Future = Future()
        done.set_result(None)
        self.in_flight[key] = (RUN_ID, JOB_ID, done)

        updated_in_flight = poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 0)])
        self.assertIsNot(updated_in_flight[key][2], done)

    def test_multiple_jobs_are_handled_independently(self) -> None:
        self._cjobs = [
            _make_lifecycle(job_id="job-new"),
            _make_lifecycle(job_id="job-running"),
            _make_lifecycle(job_id="job-fail", retry=True),
            _make_lifecycle(job_id="job-stop", stop_requested=True),
            _make_lifecycle(job_id="job-done"),
        ]
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
        self._cjobs.append(_make_lifecycle())

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
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        failed: Future = Future()
        failed.set_exception(RuntimeError("payload download failed"))
        self.in_flight[key] = (RUN_ID, JOB_ID, failed)

        with patch.object(JobLifecycle, "load_from_mlflow", return_value=lifecycle):
            updated_in_flight = poll_once(self.executor, self.in_flight)

        self.assertEqual(lifecycle.history[-1].error, "payload download failed")
        self.assertNotIn(key, updated_in_flight)

    def test_worker_exception_does_not_block_subsequent_dispatch(self) -> None:
        """After a worker raises, the next poll can redispatch the job."""
        self._cjobs.append(_make_lifecycle())
        key = ray_submission_id(RUN_ID, JOB_ID, None)
        failed: Future = Future()
        failed.set_exception(RuntimeError("boom"))
        self.in_flight[key] = (RUN_ID, JOB_ID, failed)

        with (
            patch.object(
                JobLifecycle, "load_from_mlflow", return_value=_make_lifecycle()
            ),
            patch.object(JobLifecycle, "save_to_mlflow"),
        ):
            poll_once(self.executor, self.in_flight)

        self.assertEqual(self._submitted, [(RUN_ID, JOB_ID, 0)])


class TestSubmitJobWorker(unittest.TestCase):
    """Sanity tests for ``_submit_job_worker`` under its new contract.

    The worker now always appends an ``attempt`` suffix to the submission
    id, raises on MLflow/Ray errors (no more swallowing), and does not
    write anything back to the lifecycle.
    """

    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        self.fake_s3 = FakeS3()
        self.submitted: list[dict[str, Any]] = []
        self.project_root = Path(tempfile.mkdtemp())
        (self.project_root / "pyproject.toml").write_text("[project]\nname='t'\n")
        self.original_cwd = os.getcwd()
        os.chdir(self.project_root)

        def _submit_ray_job(**kwargs: Any) -> None:
            self.submitted.append(kwargs)

        patchers = [
            patch("cortexgrid.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexgrid.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
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
        self.addCleanup(shutil.rmtree, self.fake_mlflow.artifact_root, True)

    def _seed(
        self,
        stop_requested: bool = False,
        with_payload: bool = True,
        pip_requirements: list[str] | None = None,
        delete_requested: bool = False,
    ) -> None:
        lifecycle = _make_lifecycle(
            stop_requested=stop_requested,
            pip_requirements=pip_requirements,
            delete_requested=delete_requested,
        )
        payload = None
        if with_payload:
            payload = Payload(
                experiment_name=EXPERIMENT_NAME,
                run_id=RUN_ID,
                job_id=JOB_ID,
                fn=_noop,
                args=(),
                kwargs={},
                project_code_root=str(self.project_root),
            )
        self.fake_mlflow.add_job(JOB_ID, lifecycle, payload, fake_s3=self.fake_s3)

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

    def test_delete_requested_short_circuits_before_submitting(self) -> None:
        """A job marked while its submission was already dispatched."""
        self._seed(delete_requested=True)

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
