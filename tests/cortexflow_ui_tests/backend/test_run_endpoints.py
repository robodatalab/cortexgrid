from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cortexflow.ray_util import JobStatus
from cortexflow.jobs import JobLifecycle, LifecycleEvent
from cortexflow_ui.backend.main import app


class TestRunEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch(
        "cortexflow_ui.backend.main.list_run_metrics", return_value=["loss", "accuracy"]
    )
    def test_run_metrics_returns_keys(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ["loss", "accuracy"])

    @patch(
        "cortexflow_ui.backend.main.get_metric_history",
        return_value=[
            {"step": 1, "value": 0.9, "timestamp": 1000},
            {"step": 2, "value": 0.8, "timestamp": 2000},
        ],
    )
    def test_run_metric_history_returns_points(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/metrics/loss")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["step"], 1)
        self.assertEqual(data[1]["value"], 0.8)

    @patch(
        "cortexflow_ui.backend.main.list_run_params",
        return_value={"lr": "0.001", "epochs": "10"},
    )
    def test_run_params_returns_dict(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/params")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"lr": "0.001", "epochs": "10"})

    @patch(
        "cortexflow_ui.backend.main.list_run_artifacts",
        return_value=["model.pt", "job/job-1"],
    )
    def test_run_artifacts_returns_paths(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/artifacts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ["model.pt", "job/job-1"])

    @patch(
        "cortexflow_ui.backend.main.get_mlflow_run_url",
        return_value="http://test:5000/#/experiments/1/runs/run-1",
    )
    def test_run_url_returns_mlflow_url(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/runs/run-1/url")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"url": "http://test:5000/#/experiments/1/runs/run-1"}
        )

    @patch(
        "cortexflow_ui.backend.main.list_run_artifacts",
        return_value=["payload.pkl", "lifecycle.json"],
    )
    @patch(
        "cortexflow_ui.backend.main.get_ray_job_url",
        return_value="http://test:8265/#/jobs/ray-1",
    )
    @patch(
        "cortexflow_ui.backend.main.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch("cortexflow.ray_util.list_ray_jobs_with_submission_id", return_value=[])
    @patch("cortexflow_ui.backend.main.JobLifecycle")
    def test_job_detail_returns_lifecycle_and_ray_status(
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

        response = self.client.get("/api/runs/run-1/jobs/job-1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["job_id"], "job-1")
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["history"], [])

    @patch(
        "cortexflow_ui.backend.main.list_run_artifacts",
        return_value=["payload.pkl", "lifecycle.json"],
    )
    @patch(
        "cortexflow_ui.backend.main.get_ray_job_url",
        return_value=None,
    )
    @patch(
        "cortexflow_ui.backend.main.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch("cortexflow.ray_util.list_ray_jobs_with_submission_id", return_value=[])
    @patch("cortexflow_ui.backend.main.JobLifecycle")
    def test_job_detail_returns_history_entries(
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

        response = self.client.get("/api/runs/run-1/jobs/job-1")

        self.assertEqual(response.status_code, 200)
        history = response.json()["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["attempt"], 0)
        self.assertEqual(history[0]["state"], "pending")
        self.assertEqual(history[0]["start"], "2026-04-15T10:00:00+00:00")
        self.assertEqual(history[0]["end"], "2026-04-15T10:00:05+00:00")
        self.assertEqual(history[1]["state"], "running")
        self.assertIsNone(history[1]["end"])

    @patch(
        "cortexflow_ui.backend.main.list_run_artifacts",
        return_value=["payload.pkl", "lifecycle.json"],
    )
    @patch("cortexflow_ui.backend.main.get_ray_job_url", return_value=None)
    @patch(
        "cortexflow_ui.backend.main.get_ray_job_status",
        return_value=JobStatus.RUNNING,
    )
    @patch("cortexflow.ray_util.list_ray_jobs_with_submission_id", return_value=[])
    @patch("cortexflow_ui.backend.main.JobLifecycle")
    def test_job_detail_includes_readiness_when_healthy(
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

        response = self.client.get("/api/runs/run-1/jobs/job-1")

        self.assertEqual(response.status_code, 200)
        readiness = response.json()["readiness"]
        self.assertTrue(readiness["code"])
        self.assertTrue(readiness["lifecycle"])
        self.assertIsNone(readiness["lifecycle_error"])

    @patch(
        "cortexflow_ui.backend.main.list_run_artifacts",
        return_value=["payload.pkl"],
    )
    def test_job_detail_returns_pending_when_lifecycle_missing(
        self,
        _mock_list_artifacts: MagicMock,
    ) -> None:
        response = self.client.get("/api/runs/run-1/jobs/job-1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["job_id"], "job-1")
        self.assertFalse(data["readiness"]["lifecycle"])
        self.assertIn("lifecycle.json", data["readiness"]["lifecycle_error"])
        self.assertNotIn("history", data)

    @patch(
        "cortexflow_ui.backend.main.get_ray_logs",
        return_value="installing torch...\nDone\n",
    )
    def test_ray_job_logs_returns_logs(self, _mock: MagicMock) -> None:
        response = self.client.get("/api/ray/jobs/ray-1/logs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"logs": "installing torch...\nDone\n"})

    @patch("cortexflow_ui.backend.main.get_ray_job_status")
    @patch(
        "cortexflow_ui.backend.main.list_ray_jobs_with_submission_id",
        return_value=[],
    )
    @patch("cortexflow_ui.backend.main.list_experiment_run_jobs")
    def test_run_jobs_returns_list(
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

        response = self.client.get("/api/runs/run-1/jobs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {"job_id": "j1", "status": "running", "retry": False},
                {"job_id": "j2", "status": "finished", "retry": False},
            ],
        )

    @patch(
        "cortexflow_ui.backend.main.get_ray_job_status", return_value=JobStatus.RUNNING
    )
    @patch(
        "cortexflow_ui.backend.main.list_ray_jobs_with_submission_id",
        return_value=[],
    )
    @patch("cortexflow_ui.backend.main.list_experiment_run_jobs")
    def test_run_jobs_queries_ray_list_once_regardless_of_job_count(
        self,
        mock_list: MagicMock,
        mock_list_ray_jobs: MagicMock,
        _mock_status: MagicMock,
    ) -> None:
        """Regression guard for the N+1 Ray query bug: one Ray list call
        per request, not per job."""
        mock_list.return_value = [
            JobLifecycle(experiment_name="alpha", run_id="run-1", job_id=f"j{i}")
            for i in range(5)
        ]

        response = self.client.get("/api/runs/run-1/jobs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_list_ray_jobs.call_count, 1)

    @patch("cortexflow_ui.backend.main.stop_experiment_run_jobs")
    def test_stop_run_calls_cortexflow(self, mock_stop: MagicMock) -> None:
        response = self.client.post("/api/runs/run-1/stop")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        mock_stop.assert_called_once_with("run-1")


if __name__ == "__main__":
    unittest.main()
