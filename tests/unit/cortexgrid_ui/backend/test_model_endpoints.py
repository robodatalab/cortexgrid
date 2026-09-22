from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from cortexgrid.model_serving import (
    ModelNotDeployed,
    ModelRequirements,
    ReplicaPlacement,
)

from cortexgrid_ui.backend.main import app
from cortexgrid_ui.backend.models.infra_status import PodStatus
from cortexgrid_ui.backend.streams import models_stream
from cortexgrid_ui.backend.streams.models_stream import Model

from tests.fakes import FakeS3, FakeState


def _patched_infra(s3: FakeS3) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch("cortexgrid.s3_util.get_s3_client", return_value=s3))
    stack.enter_context(
        patch("cortexgrid.s3_util.get_s3_bucket", return_value="test-bucket")
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
        bundle_fingerprint="",
    )


def _register(state: FakeState, family: str, suffix: str, run_name: str) -> None:
    state.seed_model(
        family,
        suffix,
        run_name,
        source=f"s3://test-bucket/models/{run_name}/{family}/{suffix}/weights/",
        tags={"size_bytes": "100"},
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
        self.state = FakeState().install(self)
        _reset_models_stream()
        self.addCleanup(_reset_models_stream)

    def test_delete_single_version_removes_from_registry_and_cache(self) -> None:
        s3 = FakeS3()
        s3.objects["models/boogey-46/Qwen2/instruct/weights/x"] = b"a"
        _register(self.state, "Qwen2", "instruct", "boogey-46")
        _seed_models_cache([_make_model("Qwen2", "instruct", "boogey-46")])

        with _patched_infra(s3):
            response = self.client.delete("/api/models/Qwen2/instruct/boogey-46")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state.models, {})
        self.assertEqual(
            [k for k in s3.objects if k.startswith("models/boogey-46/")], []
        )
        self.assertNotIn(
            "Qwen2/instruct/boogey-46",
            models_stream.models_cache.get(models_stream.META_TOPIC),
        )

    def test_delete_single_version_does_not_affect_other_versions(self) -> None:
        s3 = FakeS3()
        _register(self.state, "Qwen2", "instruct", "boogey-46")
        _register(self.state, "DeepSeek3", "chat", "rocky-99")
        _seed_models_cache(
            [
                _make_model("Qwen2", "instruct", "boogey-46"),
                _make_model("DeepSeek3", "chat", "rocky-99"),
            ]
        )

        with _patched_infra(s3):
            self.client.delete("/api/models/Qwen2/instruct/boogey-46")

        self.assertEqual(list(self.state.models), [("DeepSeek3", "chat", "rocky-99")])

    def test_delete_family_removes_every_version_in_family(self) -> None:
        s3 = FakeS3()
        _register(self.state, "Qwen2", "instruct", "boogey-46")
        _register(self.state, "Qwen2", "chat", "rocky-99")
        _register(self.state, "DeepSeek3", "chat", "snake-12")
        _seed_models_cache(
            [
                _make_model("Qwen2", "instruct", "boogey-46"),
                _make_model("Qwen2", "chat", "rocky-99"),
                _make_model("DeepSeek3", "chat", "snake-12"),
            ]
        )

        with _patched_infra(s3):
            response = self.client.delete("/api/models/Qwen2")

        self.assertEqual(response.status_code, 200)
        remaining_families = sorted(family for family, _, _ in self.state.models)
        self.assertEqual(remaining_families, ["DeepSeek3"])
        cache_after = models_stream.models_cache.get(models_stream.META_TOPIC)
        self.assertNotIn("Qwen2/instruct/boogey-46", cache_after)
        self.assertNotIn("Qwen2/chat/rocky-99", cache_after)


class TestModelRequirementsEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        _reset_models_stream()
        self.addCleanup(_reset_models_stream)
        self.state = FakeState().install(self)
        _register(self.state, "Qwen2", "instruct", "boogey-46")

    def _tags(self) -> dict[str, str]:
        return self.state.models[("Qwen2", "instruct", "boogey-46")]["tags"]

    def _put(self, body: dict[str, float], path: str = "") -> Any:
        return self.client.put(
            path or "/api/models/Qwen2/instruct/boogey-46/requirements",
            json=body,
        )

    def test_stores_the_requirements_on_the_model(self) -> None:
        response = self._put({"num_gpus": 1, "ram_gb": 16.0, "vram_gb": 24.0})

        self.assertEqual(response.status_code, 200)
        tags = self._tags()
        self.assertEqual(
            (tags["num_gpus"], tags["ram_gb"], tags["vram_gb"]),
            ("1.0", "16.0", "24.0"),
        )

    def test_stores_a_fraction_of_a_gpu(self) -> None:
        # A share of a card, so several small models can be served on one.
        response = self._put({"num_gpus": 0.25, "ram_gb": 4.0, "vram_gb": 6.0})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._tags()["num_gpus"], "0.25")

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
        self.assertNotIn("vram_gb", self._tags())

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
        self.state = FakeState().install(self)
        _register(self.state, "Qwen2", "instruct", "boogey-46")

    def _tags(self) -> dict[str, str]:
        return self.state.models[("Qwen2", "instruct", "boogey-46")]["tags"]

    def _put(self, config: dict[str, str], path: str = "") -> Any:
        return self.client.put(
            path or "/api/models/Qwen2/instruct/boogey-46/config",
            json={"config": config},
        )

    def test_stores_the_config_on_the_model(self) -> None:
        response = self._put({"model": "claude-opus-5"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(self._tags()["config"]),
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
            json.loads(self._tags()["config"]),
            {"model": "claude-opus-5"},
        )

    def test_rejects_a_blank_key(self) -> None:
        response = self._put({" ": "x"})

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("config", self._tags())

    def test_returns_404_for_a_model_that_is_not_registered(self) -> None:
        response = self._put(
            {"model": "claude-opus-5"},
            path="/api/models/Qwen2/instruct/missing/config",
        )

        self.assertEqual(response.status_code, 404)


class TestRunByNameEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.state = FakeState().install(self)
        self.state.seed_experiment("alpha")

    def test_returns_matching_experiment_and_run_id(self) -> None:
        self.state.seed_run("run-7", "boogey-46", experiment_name="beta")

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
        self.state.seed_run("run-7", "boogey-46", experiment_name="alpha")

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
            "cortexgrid.model_serving.status.get_serve_details", return_value=details
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


class TestDeploymentRedeployEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_redeploys_the_deployment(self) -> None:
        with patch("cortexgrid_ui.backend.main.redeploy_model") as redeploy_model:
            response = self.client.post(
                "/api/deployments/Qwen2/instruct/boogey-46/redeploy"
            )

        self.assertEqual(response.status_code, 200)
        redeploy_model.assert_called_once_with("Qwen2", "instruct", "boogey-46")

    def test_answers_not_found_for_a_model_that_is_not_deployed(self) -> None:
        with patch(
            "cortexgrid_ui.backend.main.redeploy_model",
            side_effect=ModelNotDeployed("Qwen2/instruct/boogey-46 is not deployed"),
        ):
            response = self.client.post(
                "/api/deployments/Qwen2/instruct/boogey-46/redeploy"
            )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class TestDeploymentDevicesEndpoint(unittest.TestCase):
    """The deployment card asks which machine each replica landed on. The
    endpoint joins Ray's view (an address) to kubernetes' (a node and a health
    verdict), so the card shows the same slate as the infrastructure tab
    instead of forming its own opinion."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def _get(
        self, placements: list[ReplicaPlacement], device: PodStatus | None
    ) -> Any:
        with (
            patch(
                "cortexgrid_ui.backend.main.model_replica_placements",
                return_value=placements,
            ),
            patch(
                "cortexgrid_ui.backend.main.device_for_ip", return_value=device
            ) as lookup,
        ):
            response = self.client.get(
                "/api/deployments/Qwen2/instruct/boogey-46/devices"
            )
        self.lookup = lookup
        return response

    @staticmethod
    def _pod(healthy: bool = True) -> PodStatus:
        return PodStatus(
            name="ray-worker-abcde",
            namespace="cortexgrid",
            node="dgx-spark-01",
            pod_ip="10.0.0.7",
            state="Running",
            health="ready" if healthy else "CrashLoopBackOff",
            healthy=healthy,
        )

    def test_reports_the_machine_a_replica_runs_on(self) -> None:
        placement = ReplicaPlacement(
            replica_id="r1", state="RUNNING", node_id="n1", node_ip="10.0.0.7"
        )

        response = self._get([placement], self._pod())

        self.assertEqual(response.status_code, 200)
        [replica] = response.json()
        self.assertEqual(replica["replica_id"], "r1")
        self.assertEqual(replica["device"]["node"], "dgx-spark-01")

    def test_carries_the_health_verdict_the_infra_tab_shows(self) -> None:
        placement = ReplicaPlacement(
            replica_id="r1", state="RUNNING", node_id="n1", node_ip="10.0.0.7"
        )

        response = self._get([placement], self._pod(healthy=False))

        device = response.json()[0]["device"]
        self.assertFalse(device["healthy"])
        self.assertEqual(device["health"], "CrashLoopBackOff")

    def test_a_replica_whose_host_is_unknown_still_appears(self) -> None:
        # Losing the machine must not hide the replica from the card.
        placement = ReplicaPlacement(
            replica_id="r1", state="STARTING", node_id=None, node_ip="10.0.0.9"
        )

        response = self._get([placement], None)

        [replica] = response.json()
        self.assertEqual(replica["state"], "STARTING")
        self.assertIsNone(replica["device"])

    def test_a_deployment_with_no_replicas_reports_none(self) -> None:
        self.assertEqual(self._get([], None).json(), [])
