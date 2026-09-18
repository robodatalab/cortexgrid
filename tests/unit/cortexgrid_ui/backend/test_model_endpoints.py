from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from cortexgrid.model_serving import ModelRequirements

from cortexgrid_ui.backend.main import app
from cortexgrid_ui.backend.streams import models_stream
from cortexgrid_ui.backend.streams.models_stream import Model

from tests.fakes import (
    FakeMlflowClient,
    FakeMlflowExperiment,
    FakeMlflowModelVersion,
    FakeMlflowRun,
    FakeS3,
)


def _patched_infra(s3: FakeS3, mlflow: FakeMlflowClient) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch("cortexgrid.s3_util.get_s3_client", return_value=s3))
    stack.enter_context(
        patch("cortexgrid.s3_util.get_s3_bucket", return_value="test-bucket")
    )
    stack.enter_context(
        patch("cortexgrid.model_storage.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexgrid.model_storage.get_mlflow_tracking_uri", return_value="")
    )
    stack.enter_context(
        patch("cortexgrid.model_storage.get_s3_bucket", return_value="test-bucket")
    )
    stack.enter_context(
        patch("cortexgrid.experiment.MlflowClient", return_value=mlflow)
    )
    stack.enter_context(
        patch("cortexgrid.experiment.get_mlflow_tracking_uri", return_value="")
    )
    return stack


def _make_model(
    family: str,
    suffix: str,
    run_name: str,
    requirements: ModelRequirements | None = None,
) -> Model:
    mid = models_stream.model_id(family, suffix, run_name)
    return Model(
        id=mid,
        family=family,
        suffix=suffix,
        run_name=run_name,
        created_at="2026-05-21T00:00:00Z",
        data_blob_path=f"s3://test-bucket/models/{run_name}/{family}/{suffix}/weights/",
        size_bytes=100,
        phase="ready",
        requirements=requirements or ModelRequirements(),
    )


def _make_version(family: str, suffix: str, run_name: str) -> FakeMlflowModelVersion:
    return FakeMlflowModelVersion(
        name=f"{family}__{suffix}",
        version="1",
        source=f"s3://test-bucket/models/{run_name}/{family}/{suffix}/weights/",
        run_id=f"run-{run_name}",
        tags={
            "family": family,
            "suffix": suffix,
            "run_name": run_name,
            "size_bytes": "100",
        },
    )


def _seed_models_cache(models: list[Model]) -> None:
    items = {m.id: m for m in models}
    models_stream.models_cache.set(models_stream.META_TOPIC, items)
    models_stream.models_refresher.pin(models_stream.META_TOPIC)


def _reset_models_stream() -> None:
    models_stream.models_refresher._listeners.clear()
    models_stream.models_cache._data.clear()


class TestDeleteModelEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        _reset_models_stream()
        self.addCleanup(_reset_models_stream)

    def test_delete_single_version_removes_from_mlflow_and_cache(self) -> None:
        s3 = FakeS3()
        s3.objects["models/boogey-46/Qwen2/instruct/weights/x"] = b"a"
        mlflow = FakeMlflowClient().seed(
            model_versions=[_make_version("Qwen2", "instruct", "boogey-46")]
        )
        _seed_models_cache([_make_model("Qwen2", "instruct", "boogey-46")])

        with _patched_infra(s3, mlflow):
            response = self.client.delete("/api/models/Qwen2/instruct/boogey-46")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mlflow.model_versions, [])
        self.assertEqual(
            [k for k in s3.objects if k.startswith("models/boogey-46/")], []
        )
        self.assertNotIn(
            "Qwen2/instruct/boogey-46",
            models_stream.models_cache.get(models_stream.META_TOPIC),
        )

    def test_delete_single_version_does_not_affect_other_versions(self) -> None:
        s3 = FakeS3()
        mlflow = FakeMlflowClient().seed(
            model_versions=[
                _make_version("Qwen2", "instruct", "boogey-46"),
                _make_version("DeepSeek3", "chat", "rocky-99"),
            ]
        )
        _seed_models_cache(
            [
                _make_model("Qwen2", "instruct", "boogey-46"),
                _make_model("DeepSeek3", "chat", "rocky-99"),
            ]
        )

        with _patched_infra(s3, mlflow):
            self.client.delete("/api/models/Qwen2/instruct/boogey-46")

        remaining = [v.tags["family"] for v in mlflow.model_versions]
        self.assertEqual(remaining, ["DeepSeek3"])

    def test_delete_family_removes_every_version_in_family(self) -> None:
        s3 = FakeS3()
        mlflow = FakeMlflowClient().seed(
            model_versions=[
                _make_version("Qwen2", "instruct", "boogey-46"),
                _make_version("Qwen2", "chat", "rocky-99"),
                _make_version("DeepSeek3", "chat", "snake-12"),
            ]
        )
        _seed_models_cache(
            [
                _make_model("Qwen2", "instruct", "boogey-46"),
                _make_model("Qwen2", "chat", "rocky-99"),
                _make_model("DeepSeek3", "chat", "snake-12"),
            ]
        )

        with _patched_infra(s3, mlflow):
            response = self.client.delete("/api/models/Qwen2")

        self.assertEqual(response.status_code, 200)
        remaining_families = sorted(v.tags["family"] for v in mlflow.model_versions)
        self.assertEqual(remaining_families, ["DeepSeek3"])
        cache_after = models_stream.models_cache.get(models_stream.META_TOPIC)
        self.assertNotIn("Qwen2/instruct/boogey-46", cache_after)
        self.assertNotIn("Qwen2/chat/rocky-99", cache_after)


class TestModelRequirementsEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        _reset_models_stream()
        self.addCleanup(_reset_models_stream)
        self.mlflow = FakeMlflowClient().seed(
            model_versions=[_make_version("Qwen2", "instruct", "boogey-46")]
        )

    def _put(self, body: dict[str, float], path: str = "") -> Any:
        with _patched_infra(FakeS3(), self.mlflow):
            return self.client.put(
                path or "/api/models/Qwen2/instruct/boogey-46/requirements",
                json=body,
            )

    def test_stores_the_requirements_on_the_model(self) -> None:
        response = self._put({"num_gpus": 1, "ram_gb": 16.0, "vram_gb": 24.0})

        self.assertEqual(response.status_code, 200)
        tags = self.mlflow.model_versions[0].tags
        self.assertEqual(
            (tags["num_gpus"], tags["ram_gb"], tags["vram_gb"]),
            ("1", "16.0", "24.0"),
        )

    def test_pushes_the_edit_into_the_models_stream(self) -> None:
        _seed_models_cache([_make_model("Qwen2", "instruct", "boogey-46")])

        self._put({"num_gpus": 1, "ram_gb": 16.0, "vram_gb": 24.0})

        cached = models_stream.models_cache.get(models_stream.META_TOPIC)
        self.assertEqual(
            cached["Qwen2/instruct/boogey-46"].requirements,
            ModelRequirements(num_gpus=1, ram_gb=16.0, vram_gb=24.0),
        )

    def test_rejects_vram_without_a_gpu(self) -> None:
        response = self._put({"num_gpus": 0, "ram_gb": 0.0, "vram_gb": 24.0})

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("vram_gb", self.mlflow.model_versions[0].tags)

    def test_returns_404_for_a_model_that_is_not_registered(self) -> None:
        response = self._put(
            {"num_gpus": 1, "ram_gb": 16.0, "vram_gb": 24.0},
            path="/api/models/Qwen2/instruct/missing/requirements",
        )

        self.assertEqual(response.status_code, 404)


class TestModelConfigEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        _reset_models_stream()
        self.addCleanup(_reset_models_stream)
        self.mlflow = FakeMlflowClient().seed(
            model_versions=[_make_version("Qwen2", "instruct", "boogey-46")]
        )

    def _put(self, config: dict[str, str], path: str = "") -> Any:
        with _patched_infra(FakeS3(), self.mlflow):
            return self.client.put(
                path or "/api/models/Qwen2/instruct/boogey-46/config",
                json={"config": config},
            )

    def test_stores_the_config_on_the_model(self) -> None:
        response = self._put({"model": "claude-opus-5"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(self.mlflow.model_versions[0].tags["config"]),
            {"model": "claude-opus-5"},
        )

    def test_pushes_the_edit_into_the_models_stream(self) -> None:
        _seed_models_cache([_make_model("Qwen2", "instruct", "boogey-46")])

        self._put({"model": "claude-opus-5"})

        cached = models_stream.models_cache.get(models_stream.META_TOPIC)
        self.assertEqual(
            cached["Qwen2/instruct/boogey-46"].config, {"model": "claude-opus-5"}
        )

    def test_removes_a_key_left_out_of_the_mapping(self) -> None:
        self._put({"model": "claude-opus-5", "region": "eu"})

        self._put({"model": "claude-opus-5"})

        self.assertEqual(
            json.loads(self.mlflow.model_versions[0].tags["config"]),
            {"model": "claude-opus-5"},
        )

    def test_rejects_a_blank_key(self) -> None:
        response = self._put({" ": "x"})

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("config", self.mlflow.model_versions[0].tags)

    def test_returns_404_for_a_model_that_is_not_registered(self) -> None:
        response = self._put(
            {"model": "claude-opus-5"},
            path="/api/models/Qwen2/instruct/missing/config",
        )

        self.assertEqual(response.status_code, 404)


class TestRunByNameEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_returns_matching_experiment_and_run_id(self) -> None:
        mlflow = FakeMlflowClient().seed(
            experiments=[
                FakeMlflowExperiment(experiment_id="e1", name="alpha"),
                FakeMlflowExperiment(experiment_id="e2", name="beta"),
            ],
            runs=[
                FakeMlflowRun(
                    run_id="run-7", run_name="boogey-46", experiment_id="e2"
                ),
            ],
        )

        with _patched_infra(FakeS3(), mlflow):
            response = self.client.get("/api/runs/by-name/boogey-46")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "experiment_name": "beta",
                "run_id": "run-7",
                "run_name": "boogey-46",
            },
        )

    def test_returns_404_when_no_run_with_that_name(self) -> None:
        mlflow = FakeMlflowClient().seed(
            experiments=[FakeMlflowExperiment(experiment_id="e1", name="alpha")],
            runs=[],
        )

        with _patched_infra(FakeS3(), mlflow):
            response = self.client.get("/api/runs/by-name/missing-run")

        self.assertEqual(response.status_code, 404)


class TestDeploymentMessagesEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_returns_controller_messages_for_the_deployment(self) -> None:
        details = {
            "applications": {
                "Qwen2__instruct__boogey-46": {
                    "status": "DEPLOY_FAILED",
                    "message": "Traceback: boom",
                    "deployments": {},
                },
            }
        }
        with patch(
            "cortexgrid.model_serving.get_serve_details", return_value=details
        ):
            response = self.client.get(
                "/api/deployments/Qwen2/instruct/boogey-46/messages"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "source": "application",
                    "status": "DEPLOY_FAILED",
                    "message": "Traceback: boom",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
