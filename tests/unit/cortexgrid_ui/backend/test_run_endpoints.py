from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cortexgrid.jobs import JobLifecycle, LifecycleEvent
from cortexgrid.ray_util import JobStatus
from cortexgrid_ui.backend.streams import experiments_stream as stream_mod
from cortexgrid_ui.backend.streams.job_details_stream import poll_job, tarball_exists
from cortexgrid_ui.backend.main import app
from cortexgrid_ui.backend.streams.run_jobs_stream import Job, list_run_jobs


class TestSimpleEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch(
        "cortexgrid_ui.backend.main.get_ray_logs",
        return_value="installing torch...\nDone\n",
    )
    def test_ray_job_logs_returns_logs(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/ray/jobs/ray-1/logs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"logs": "installing torch...\nDone\n"})

    @patch("cortexgrid_ui.backend.main.stop_experiment_run_jobs")
    def test_stop_run_calls_cortexgrid(self, mock_stop: MagicMock) -> None:
        response = self.client.post("/api/runs/run-1/stop")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        mock_stop.assert_called_once_with("run-1")


class TestListRunJobs(unittest.TestCase):
    @patch("cortexgrid_ui.backend.streams.job_status.get_ray_job_status")
    @patch(
        "cortexgrid_ui.backend.streams.run_jobs_stream.list_ray_jobs_with_submission_id",
        return_value=["run-1-j1-0", "run-1-j2-0"],
    )
    @patch("cortexgrid_ui.backend.streams.run_jobs_stream.list_experiment_run_jobs")
    def test_returns_list_with_status_per_job(
        self,
        mock_list: MagicMock,
        _mock_list_ray_jobs: MagicMock,
        mock_status: MagicMock,
    ) -> None:
        mock_list.return_value = [
            JobLifecycle(experiment_name="alpha", run_id="run-1", job_id="j1"),
            JobLifecycle(experiment_name="alpha", run_id="run-1", job_id="j2"),
        ]
        mock_status.side_effect = [JobStatus.RUNNING, JobStatus.FINISHED]

        result = list_run_jobs("run-1")

        self.assertEqual(
            result,
            {
                "j1": Job(job_id="j1", status="running", retry=False),
                "j2": Job(job_id="j2", status="finished", retry=False),
            },
        )

    @patch(
        "cortexgrid_ui.backend.streams.job_status.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch(
        "cortexgrid_ui.backend.streams.run_jobs_stream.list_ray_jobs_with_submission_id",
        return_value=[],
    )
    @patch("cortexgrid_ui.backend.streams.run_jobs_stream.list_experiment_run_jobs")
    def test_queries_ray_list_once_regardless_of_job_count(
        self,
        mock_list: MagicMock,
        mock_list_ray_jobs: MagicMock,
        _mock_status: MagicMock,
    ) -> None:
        mock_list.return_value = [
            JobLifecycle(experiment_name="alpha", run_id="run-1", job_id=f"j{i}")
            for i in range(5)
        ]

        list_run_jobs("run-1")

        self.assertEqual(mock_list_ray_jobs.call_count, 1)


class TestPollJob(unittest.TestCase):
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.list_run_artifacts",
        return_value=["payload.pkl", "lifecycle.json"],
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.get_ray_job_url",
        return_value="http://test:8265/#/jobs/ray-1",
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_status.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch(
        "cortexgrid.ray_util.list_ray_jobs_with_submission_id",
        return_value=["run-1-job-1-0"],
    )
    @patch("cortexgrid_ui.backend.streams.job_details_stream.JobLifecycle")
    def test_returns_lifecycle_and_ray_status(
        self,
        mock_lifecycle_cls: MagicMock,
        _mock_list_ray_jobs: MagicMock,
        _mock_status: MagicMock,
        _mock_ray_url: MagicMock,
        _mock_list_artifacts: MagicMock,
    ) -> None:
        mock_lifecycle_cls.load_from_mlflow.return_value = JobLifecycle(
            experiment_name="alpha",
            run_id="run-1",
            job_id="job-1",
        )

        data = poll_job(("run-1", "job-1"))["job-1"]

        self.assertEqual(data.job_id, "job-1")
        self.assertEqual(data.status, "running")
        self.assertEqual(data.history, [])

    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.list_run_artifacts",
        return_value=["payload.pkl", "lifecycle.json"],
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.get_ray_job_url", return_value=None
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_status.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch("cortexgrid.ray_util.list_ray_jobs_with_submission_id", return_value=[])
    @patch("cortexgrid_ui.backend.streams.job_details_stream.JobLifecycle")
    def test_returns_history_entries(
        self,
        mock_lifecycle_cls: MagicMock,
        _mock_list_ray_jobs: MagicMock,
        _mock_status: MagicMock,
        _mock_ray_url: MagicMock,
        _mock_list_artifacts: MagicMock,
    ) -> None:
        mock_lifecycle_cls.load_from_mlflow.return_value = JobLifecycle(
            experiment_name="alpha",
            run_id="run-1",
            job_id="job-1",
            history=[
                LifecycleEvent(
                    attempt=0,
                    state="pending",
                    start="2026-04-15T10:00:00+00:00",
                    end="2026-04-15T10:00:05+00:00",
                ),
                LifecycleEvent(
                    attempt=0,
                    state="running",
                    start="2026-04-15T10:00:05+00:00",
                    end=None,
                ),
            ],
        )

        history = poll_job(("run-1", "job-1"))["job-1"].history

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].state, "pending")
        self.assertEqual(history[1].state, "running")
        self.assertIsNone(history[1].end)

    @patch("cortexgrid_ui.backend.streams.job_details_stream.tarball_exists", return_value=True)
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.list_run_artifacts",
        return_value=["manifest.json", "lifecycle.json"],
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.get_ray_job_url", return_value=None
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_status.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch("cortexgrid.ray_util.list_ray_jobs_with_submission_id", return_value=[])
    @patch("cortexgrid_ui.backend.streams.job_details_stream.JobLifecycle")
    def test_includes_readiness_when_healthy(
        self,
        mock_lifecycle_cls: MagicMock,
        _mock_list_ray_jobs: MagicMock,
        _mock_status: MagicMock,
        _mock_ray_url: MagicMock,
        _mock_list_artifacts: MagicMock,
        _mock_tarball: MagicMock,
    ) -> None:
        mock_lifecycle_cls.load_from_mlflow.return_value = JobLifecycle(
            experiment_name="alpha",
            run_id="run-1",
            job_id="job-1",
        )

        readiness = poll_job(("run-1", "job-1"))["job-1"].readiness

        self.assertTrue(readiness.code)
        self.assertTrue(readiness.lifecycle)
        self.assertIsNone(readiness.lifecycle_error)

    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.list_run_artifacts",
        return_value=[],
    )
    def test_returns_pending_when_lifecycle_missing(
        self,
        _mock_list_artifacts: MagicMock,
    ) -> None:
        data = poll_job(("run-1", "job-1"))["job-1"]

        self.assertEqual(data.job_id, "job-1")
        self.assertFalse(data.readiness.lifecycle)
        self.assertIn("lifecycle.json", data.readiness.lifecycle_error)
        self.assertIsNone(data.history)

    @patch("cortexgrid_ui.backend.streams.job_details_stream.tarball_exists", return_value=True)
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.list_run_artifacts",
        return_value=["lifecycle.json"],
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.get_ray_job_url", return_value=None
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_status.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch("cortexgrid.ray_util.list_ray_jobs_with_submission_id", return_value=[])
    @patch("cortexgrid_ui.backend.streams.job_details_stream.JobLifecycle")
    def test_code_not_ready_when_manifest_missing(
        self,
        mock_lifecycle_cls: MagicMock,
        _mock_list_ray_jobs: MagicMock,
        _mock_status: MagicMock,
        _mock_ray_url: MagicMock,
        _mock_list_artifacts: MagicMock,
        mock_tarball: MagicMock,
    ) -> None:
        mock_lifecycle_cls.load_from_mlflow.return_value = JobLifecycle(
            experiment_name="alpha",
            run_id="run-1",
            job_id="job-1",
        )

        readiness = poll_job(("run-1", "job-1"))["job-1"].readiness

        self.assertFalse(readiness.code)
        self.assertTrue(readiness.lifecycle)
        mock_tarball.assert_not_called()

    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.tarball_exists", return_value=False
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.list_run_artifacts",
        return_value=["manifest.json", "lifecycle.json"],
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_details_stream.get_ray_job_url", return_value=None
    )
    @patch(
        "cortexgrid_ui.backend.streams.job_status.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch("cortexgrid.ray_util.list_ray_jobs_with_submission_id", return_value=[])
    @patch("cortexgrid_ui.backend.streams.job_details_stream.JobLifecycle")
    def test_code_not_ready_when_tarball_missing(
        self,
        mock_lifecycle_cls: MagicMock,
        _mock_list_ray_jobs: MagicMock,
        _mock_status: MagicMock,
        _mock_ray_url: MagicMock,
        _mock_list_artifacts: MagicMock,
        mock_tarball: MagicMock,
    ) -> None:
        mock_lifecycle_cls.load_from_mlflow.return_value = JobLifecycle(
            experiment_name="alpha",
            run_id="run-1",
            job_id="job-1",
        )

        readiness = poll_job(("run-1", "job-1"))["job-1"].readiness

        self.assertFalse(readiness.code)
        self.assertTrue(readiness.lifecycle)
        mock_tarball.assert_called_once_with("run-1", "job-1")


class TestTarballExists(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())
        manifest = {
            "code_tarball_uri": "s3://ray-checkpoints/job/job-1/project_code_root.tar.gz"
        }
        self.manifest_path = self.tmpdir / "manifest.json"
        self.manifest_path.write_text(json.dumps(manifest))

        self.fake_mlflow = MagicMock()
        self.fake_mlflow.download_artifacts.return_value = str(self.manifest_path)
        self.fake_s3 = MagicMock()

        patchers = [
            patch(
                "cortexgrid_ui.backend.streams.job_details_stream.MlflowClient",
                return_value=self.fake_mlflow,
            ),
            patch(
                "cortexgrid_ui.backend.streams.job_details_stream.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch(
                "cortexgrid_ui.backend.streams.job_details_stream.s3_util.get_s3_client",
                return_value=self.fake_s3,
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_returns_true_when_head_object_succeeds(self) -> None:
        self.fake_s3.head_object.return_value = {"ContentLength": 123}

        self.assertTrue(tarball_exists("run-1", "job-1"))
        self.fake_s3.head_object.assert_called_once_with(
            Bucket="ray-checkpoints",
            Key="job/job-1/project_code_root.tar.gz",
        )

    def test_returns_false_when_head_object_raises(self) -> None:
        self.fake_s3.head_object.side_effect = Exception("404 NoSuchKey")

        self.assertFalse(tarball_exists("run-1", "job-1"))


class TestExperimentsStream(unittest.TestCase):
    def test_resolvers_read_from_cache(self) -> None:
        run = stream_mod.Run(
            experiment_name="alpha",
            run_id="r1",
            run_name="alpha-run",
            jobs=[],
        )
        stream_mod.runs_cache.set("alpha", {"alpha-run": run})
        try:
            self.assertEqual(stream_mod.resolve_run_id("alpha-run"), "r1")
            self.assertEqual(stream_mod.resolve_run_name("r1"), "alpha-run")
            self.assertEqual(stream_mod.runs_for_experiment("alpha"), ["alpha-run"])
        finally:
            stream_mod.runs_cache.clear("alpha")


if __name__ == "__main__":
    unittest.main()
