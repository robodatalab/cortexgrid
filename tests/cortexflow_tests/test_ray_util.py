from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import cortexflow


class FakeJobSubmissionClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.statuses: dict[str, str] = {}
        self.logs_by_id: dict[str, str] = {}

    def get_job_status(self, job_id: str) -> SimpleNamespace:
        return SimpleNamespace(value=self.statuses.get(job_id, "PENDING"))

    def get_job_logs(self, job_id: str) -> str:
        return self.logs_by_id.get(job_id, "")


class TestRayUtil(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_jsc = FakeJobSubmissionClient()
        patcher = patch(
            "cortexflow.ray_util.JobSubmissionClient",
            return_value=self.fake_jsc,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_ray_status_returns_status_string(self) -> None:
        self.fake_jsc.statuses["job-1"] = "RUNNING"

        self.assertEqual(cortexflow.get_ray_status("job-1"), "RUNNING")

    def test_get_ray_logs_returns_log_text(self) -> None:
        self.fake_jsc.logs_by_id["job-1"] = "hello from the cluster"

        self.assertEqual(
            cortexflow.get_ray_logs("job-1"), "hello from the cluster"
        )


if __name__ == "__main__":
    unittest.main()
