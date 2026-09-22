from __future__ import annotations

import unittest
from dataclasses import asdict
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
from parameterized import parameterized
from psycopg.errors import ForeignKeyViolation

from cortexgrid.jobs import JobLifecycle, LifecycleEvent
from jobs_control_plane import db
from jobs_control_plane.api import app


class _FakeDB:
    """In-memory stand-in for the jobs_control_plane.db functions these tests
    reach. Rows are keyed by (table, *primary key)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, ...], Any] = {}

    def put_experiment(self, name: str, mlflow_experiment_id: str) -> dict[str, Any]:
        # ON CONFLICT DO NOTHING: the experiment recorded first stands.
        return self.rows.setdefault(
            ("experiments", name),
            {"name": name, "mlflow_experiment_id": mlflow_experiment_id},
        )

    def put_job(self, lifecycle: dict[str, Any]) -> None:
        self.rows[("jobs", lifecycle["run_id"], lifecycle["job_id"])] = lifecycle

    def get_job(self, run_id: str, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(("jobs", run_id, job_id))

    def get_manifest(self, run_id: str, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(("job_manifests", run_id, job_id))

    def put_result(self, run_id: str, job_id: str, result: bytes) -> None:
        self.rows[("job_results", run_id, job_id)] = result

    def get_result(self, run_id: str, job_id: str) -> bytes | None:
        return self.rows.get(("job_results", run_id, job_id))

    def put_checkpoint(
        self, run_id: str, prefix: str, manifest: dict[str, Any]
    ) -> None:
        self.rows[("checkpoints", run_id, prefix)] = manifest

    def get_checkpoint(self, run_id: str, prefix: str) -> dict[str, Any] | None:
        return self.rows.get(("checkpoints", run_id, prefix))

    def get_model(
        self, family: str, suffix: str, run_name: str
    ) -> dict[str, Any] | None:
        return self.rows.get(("models", family, suffix, run_name))

    def patch_model_tags(
        self, family: str, suffix: str, run_name: str, tags: dict[str, str]
    ) -> bool:
        model = self.get_model(family, suffix, run_name)
        if model is None:
            return False
        model["tags"].update(tags)
        return True

    def get_deployment(
        self, family: str, suffix: str, run_name: str, config_fingerprint: str
    ) -> dict[str, Any] | None:
        return self.rows.get(
            ("deployments", family, suffix, run_name, config_fingerprint)
        )


class TestStateApi(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _FakeDB()
        # patch.multiple refuses a name db lacks, so the fake cannot drift.
        patcher = patch.multiple(
            db,
            **{
                name: getattr(self.db, name)
                for name in vars(_FakeDB)
                if not name.startswith("_")
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)

    @parameterized.expand(
        [
            ("job", "/runs/run-1/jobs/job-1"),
            ("manifest", "/runs/run-1/jobs/job-1/manifest"),
            ("result", "/runs/run-1/jobs/job-1/result"),
            ("model", "/models/Qwen2/instruct/boogey-46"),
            ("deployment", "/deployments/Qwen2/instruct/boogey-46"),
        ]
    )
    def test_missing_record_is_404(self, name: str, path: str) -> None:
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_deployments_of_one_model_are_told_apart_by_config_fingerprint(
        self,
    ) -> None:
        path = ("deployments", "Qwen3", "8B", "imported")
        self.db.rows[(*path, "")] = {"config": {}}
        self.db.rows[(*path, "5f0c1d2e3a4b")] = {"config": {"thinking": "false"}}

        unconfigured = self.client.get("/deployments/Qwen3/8B/imported")
        without_thinking = self.client.get(
            "/deployments/Qwen3/8B/imported",
            params={"config_fingerprint": "5f0c1d2e3a4b"},
        )

        self.assertEqual(unconfigured.json(), {"config": {}})
        self.assertEqual(without_thinking.json(), {"config": {"thinking": "false"}})

    def test_job_lifecycle_round_trips(self) -> None:
        lifecycle = JobLifecycle(
            experiment_name="exp",
            run_id="run-1",
            job_id="job-1",
            retry=True,
            pip_requirements=["tqdm==4.67.3"],
            history=[
                LifecycleEvent(
                    attempt=0,
                    state="running",
                    start="2026-04-15T10:00:00+00:00",
                    ray_job_id="run-1-job-1-0",
                )
            ],
        )

        put = self.client.put("/runs/run-1/jobs/job-1", json=asdict(lifecycle))
        got = self.client.get("/runs/run-1/jobs/job-1")

        self.assertEqual(put.status_code, 200)
        self.assertEqual(JobLifecycle.from_dict(got.json()), lifecycle)

    def test_binary_result_round_trips(self) -> None:
        # Not valid UTF-8, so a text round trip anywhere would corrupt it.
        blob = bytes(range(256))

        put = self.client.put(
            "/runs/run-1/jobs/job-1/result",
            content=blob,
            headers={"Content-Type": "application/octet-stream"},
        )
        got = self.client.get("/runs/run-1/jobs/job-1/result")

        self.assertEqual(put.status_code, 200)
        self.assertEqual(got.content, blob)
        self.assertEqual(got.headers["content-type"], "application/octet-stream")

    def test_patch_model_tags_is_404_when_db_has_no_row(self) -> None:
        path = "/models/Qwen2/instruct/boogey-46"

        missing = self.client.patch(f"{path}/tags", json={"stage": "prod"})
        self.db.rows[("models", "Qwen2", "instruct", "boogey-46")] = {"tags": {}}
        present = self.client.patch(f"{path}/tags", json={"stage": "prod"})

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(present.status_code, 200)
        self.assertEqual(self.client.get(path).json(), {"tags": {"stage": "prod"}})

    def test_write_under_an_unknown_run_is_404(self) -> None:
        """The schema's foreign keys turn a write under a run cortexgrid has
        no record of into a ForeignKeyViolation."""
        lifecycle = JobLifecycle(experiment_name="exp", run_id="gone", job_id="job-1")

        with patch.object(
            db, "put_job", side_effect=ForeignKeyViolation("no run 'gone'")
        ):
            response = self.client.put("/runs/gone/jobs/job-1", json=asdict(lifecycle))

        self.assertEqual(response.status_code, 404)

    def test_put_experiment_answers_the_recorded_experiment(self) -> None:
        """A concurrent creator learns which MLflow experiment won."""
        first = self.client.put("/experiments/exp", json={"mlflow_experiment_id": "1"})
        second = self.client.put("/experiments/exp", json={"mlflow_experiment_id": "2"})

        self.assertEqual(first.json(), {"name": "exp", "mlflow_experiment_id": "1"})
        self.assertEqual(second.json(), {"name": "exp", "mlflow_experiment_id": "1"})

    def test_checkpoint_prefix_keeps_its_quoted_slash(self) -> None:
        """cortexgrid.state quotes a prefix's slash; db sees it unquoted."""
        manifest = {"files": ["model.safetensors"]}

        put = self.client.put(
            "/runs/run-1/checkpoints/checkpoint%2Fjob-1", json=manifest
        )
        got = self.client.get("/runs/run-1/checkpoints/checkpoint%2Fjob-1")

        self.assertEqual(put.status_code, 200)
        self.assertEqual(
            self.db.rows, {("checkpoints", "run-1", "checkpoint/job-1"): manifest}
        )
        self.assertEqual(got.json(), manifest)


if __name__ == "__main__":
    unittest.main()
