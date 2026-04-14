from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import cloudpickle  # type: ignore

from cortexflow.experiment import Experiment
from cortexflow.jobs import JobLifecycle, Payload
from jobs_control_plane.server import (
    _dispatch_job,
    _ray_submission_id,
    _submit_job_worker,
    poll_once,
)


EXPERIMENT_NAME = "exp"
RUN_ID = "run-1"
JOB_ID = "job-1"


def _make_experiment() -> Experiment:
    return Experiment(experiment_name=EXPERIMENT_NAME, run_id=RUN_ID)


class FakeMLflow:
    """Fake MlflowClient backed by a real temp directory."""

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
        return str(self.artifact_root / artifact_path)

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
    ) -> None:
        job_dir = self.artifact_root / "job" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "lifecycle.json").write_text(lifecycle.to_json())
        if payload is not None:
            project_dest = job_dir / "project_code_root"
            shutil.copytree(
                payload.project_code_root,
                str(project_dest),
                dirs_exist_ok=True,
            )
            (project_dest / "payload.pkl").write_bytes(cloudpickle.dumps(payload))


class FakeRay:
    """Backs the cortexflow ray wrappers that ``server.py`` calls.

    The three wrappers are patched at the ``jobs_control_plane.server``
    import site (see :meth:`install`) and each one routes into a method
    on this object. That keeps test state in one place while still
    exercising the wrapper boundary the production code goes through.

    Semantics:
    - ``get_ray_status`` raises for unknown submission_ids (matching
      Ray's "job not found" behaviour that idempotent submission relies
      on).
    - ``submit_ray_job`` records the kwargs; refuses duplicates so a
      double-submit is observable.
    - ``stop_ray_job`` records the call.
    """

    def __init__(self) -> None:
        self.submissions: dict[str, dict[str, Any]] = {}
        self.stopped: list[str] = []
        self.submit_raises: Exception | None = None

    def get_ray_status(self, submission_id: str) -> str:
        if submission_id not in self.submissions:
            raise RuntimeError(f"Job {submission_id} not found")
        return "STOPPED" if submission_id in self.stopped else "RUNNING"

    def submit_ray_job(self, **kwargs: Any) -> None:
        if self.submit_raises is not None:
            raise self.submit_raises
        submission_id = kwargs["submission_id"]
        if submission_id in self.submissions:
            raise RuntimeError(f"Job {submission_id} already exists")
        self.submissions[submission_id] = kwargs

    def stop_ray_job(self, submission_id: str) -> None:
        self.stopped.append(submission_id)

    def install(self, case: unittest.TestCase) -> None:
        """Patch the three cortexflow wrappers at their server.py import
        site so every call routes through this FakeRay instance.
        """
        patchers = [
            patch(
                "jobs_control_plane.server.get_ray_status",
                side_effect=self.get_ray_status,
            ),
            patch(
                "jobs_control_plane.server.stop_ray_job",
                side_effect=self.stop_ray_job,
            ),
            patch(
                "jobs_control_plane.server.submit_ray_job",
                side_effect=self.submit_ray_job,
            ),
        ]
        for p in patchers:
            p.start()
            case.addCleanup(p.stop)


def _make_lifecycle(**overrides: Any) -> JobLifecycle:
    return JobLifecycle(
        experiment_name=EXPERIMENT_NAME,
        run_id=RUN_ID,
        job_id=JOB_ID,
        **overrides,
    )


class TestDispatchJob(unittest.TestCase):
    """Unit tests for the per-job decision step.

    These test the pure dispatch logic without running a worker — the
    executor is a MagicMock and we assert on its ``submit`` calls.
    """

    def setUp(self) -> None:
        self.fake_ray = FakeRay()
        self.fake_ray.install(self)
        self.executor = MagicMock()
        self.executor.submit.return_value = Future()
        self.in_flight: dict[str, Future] = {}

    def _dispatch(self, lifecycle: JobLifecycle) -> None:
        _dispatch_job(self.executor, self.in_flight, lifecycle)

    def test_unsubmitted_job_is_handed_to_worker_pool(self) -> None:
        self._dispatch(_make_lifecycle())

        self.executor.submit.assert_called_once_with(
            _submit_job_worker, RUN_ID, JOB_ID
        )
        self.assertIn(_ray_submission_id(RUN_ID, JOB_ID), self.in_flight)

    def test_unsubmitted_job_with_stop_requested_is_skipped(self) -> None:
        self._dispatch(_make_lifecycle(stop_requested=True))

        self.executor.submit.assert_not_called()
        self.assertEqual(self.in_flight, {})

    def test_unsubmitted_job_already_in_flight_is_skipped(self) -> None:
        key = _ray_submission_id(RUN_ID, JOB_ID)
        self.in_flight[key] = Future()  # not done
        self._dispatch(_make_lifecycle())

        self.executor.submit.assert_not_called()

    def test_submitted_job_without_stop_is_not_touched(self) -> None:
        self._dispatch(_make_lifecycle(ray_job_id="sid"))

        self.executor.submit.assert_not_called()
        self.assertEqual(self.fake_ray.stopped, [])

    def test_submitted_job_with_stop_requested_calls_ray_stop(self) -> None:
        self.fake_ray.submissions["sid"] = {}  # exists in Ray, running
        self._dispatch(_make_lifecycle(ray_job_id="sid", stop_requested=True))

        self.assertEqual(self.fake_ray.stopped, ["sid"])
        self.executor.submit.assert_not_called()

    def test_submitted_job_with_stop_requested_but_terminal_in_ray(self) -> None:
        # Ray says STOPPED already — nothing to do.
        self.fake_ray.submissions["sid"] = {}
        self.fake_ray.stopped.append("sid")
        self._dispatch(_make_lifecycle(ray_job_id="sid", stop_requested=True))

        # stop_ray_job should NOT have been called a second time.
        self.assertEqual(self.fake_ray.stopped, ["sid"])

    def test_ray_query_failure_does_not_raise(self) -> None:
        # Unknown submission_id in FakeRay → get_ray_status raises.
        self._dispatch(_make_lifecycle(ray_job_id="unknown", stop_requested=True))
        # If we got here, no exception propagated. stop_ray_job should
        # not have been called because the status query failed.
        self.assertEqual(self.fake_ray.stopped, [])


class TestPollOnce(unittest.TestCase):
    """Integration-ish tests for poll_once dispatch + in_flight reaping."""

    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        self.fake_ray = FakeRay()
        self.fake_ray.install(self)
        self.executor = MagicMock()
        self.executor.submit.return_value = Future()
        self.in_flight: dict[str, Future] = {}

        patchers = [
            patch(
                "jobs_control_plane.server.list_experiments",
                return_value=[_make_experiment()],
            ),
            patch(
                "cortexflow.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_unsubmitted_job_is_dispatched(self) -> None:
        self.fake_mlflow.add_job(JOB_ID, _make_lifecycle())

        poll_once(self.executor, self.in_flight)

        self.executor.submit.assert_called_once_with(
            _submit_job_worker, RUN_ID, JOB_ID
        )

    def test_done_futures_are_reaped_before_dispatch(self) -> None:
        key = _ray_submission_id(RUN_ID, JOB_ID)
        done_future: Future = Future()
        done_future.set_result(None)
        self.in_flight[key] = done_future
        self.fake_mlflow.add_job(JOB_ID, _make_lifecycle())

        poll_once(self.executor, self.in_flight)

        # The done future was reaped, so dispatch re-submitted.
        self.executor.submit.assert_called_once_with(
            _submit_job_worker, RUN_ID, JOB_ID
        )

    def test_in_flight_job_is_not_redispatched(self) -> None:
        key = _ray_submission_id(RUN_ID, JOB_ID)
        self.in_flight[key] = Future()  # not done
        self.fake_mlflow.add_job(JOB_ID, _make_lifecycle())

        poll_once(self.executor, self.in_flight)

        self.executor.submit.assert_not_called()


class TestSubmitJobWorker(unittest.TestCase):
    """Exercises _submit_job_worker with faked MLflow and Ray."""

    def setUp(self) -> None:
        self.fake_mlflow = FakeMLflow()
        self.fake_ray = FakeRay()
        self.fake_ray.install(self)
        self.project_root = Path(tempfile.mkdtemp())
        (self.project_root / "pyproject.toml").write_text("[project]\nname='t'\n")
        self.original_cwd = os.getcwd()
        os.chdir(self.project_root)

        patchers = [
            patch("cortexflow.jobs.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexflow.jobs.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("jobs_control_plane.server.set_runs_on_server"),
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
        ray_job_id: str | None = None,
        with_payload: bool = True,
    ) -> None:
        lifecycle = _make_lifecycle(
            stop_requested=stop_requested, ray_job_id=ray_job_id
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
        self.fake_mlflow.add_job(JOB_ID, lifecycle, payload)

    def _loaded(self) -> JobLifecycle:
        return JobLifecycle.load_from_mlflow(RUN_ID, JOB_ID)

    def test_happy_path_submits_to_ray_and_persists_ray_job_id(self) -> None:
        self._seed()

        _submit_job_worker(RUN_ID, JOB_ID)

        submission_id = _ray_submission_id(RUN_ID, JOB_ID)
        self.assertIn(submission_id, self.fake_ray.submissions)
        self.assertEqual(self._loaded().ray_job_id, submission_id)

    def test_idempotent_when_ray_already_has_the_submission(self) -> None:
        # Seed Ray with an existing submission for the deterministic id.
        submission_id = _ray_submission_id(RUN_ID, JOB_ID)
        self.fake_ray.submissions[submission_id] = {"prior": True}
        self._seed()

        _submit_job_worker(RUN_ID, JOB_ID)

        # submit_job was NOT called — prior dict entry unchanged.
        self.assertEqual(
            self.fake_ray.submissions[submission_id], {"prior": True}
        )
        self.assertEqual(self._loaded().ray_job_id, submission_id)

    def test_stop_requested_short_circuits_submission(self) -> None:
        self._seed(stop_requested=True)

        _submit_job_worker(RUN_ID, JOB_ID)

        self.assertEqual(self.fake_ray.submissions, {})
        self.assertIsNone(self._loaded().ray_job_id)

    def test_payload_load_failure_does_not_raise_or_mutate(self) -> None:
        self._seed(with_payload=False)

        _submit_job_worker(RUN_ID, JOB_ID)

        self.assertEqual(self.fake_ray.submissions, {})
        self.assertIsNone(self._loaded().ray_job_id)

    def test_ray_submit_failure_does_not_raise_or_mutate(self) -> None:
        self._seed()
        self.fake_ray.submit_raises = RuntimeError("cluster is full")

        _submit_job_worker(RUN_ID, JOB_ID)

        self.assertEqual(self.fake_ray.submissions, {})
        self.assertIsNone(self._loaded().ray_job_id)


def _noop() -> None:
    pass


if __name__ == "__main__":
    unittest.main()
