from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from cortexgrid.model_serving import (
    BundleMetadata,
    _wait_for_application_running,
    deploy_model,
    list_deployed_models,
    model_serving_status,
    undeploy_model,
)


_FAKE_META = BundleMetadata(
    bundle_url="s3://bucket/stub.zip",
    class_import_path="stub:Stub",
)


class FakeServeState:
    """In-memory stand-in for Ray Serve's declarative app registry."""

    def __init__(self) -> None:
        self.apps: dict[str, dict[str, Any]] = {}
        self.status: str = "RUNNING"
        self.message: str = ""

    def get_details(self) -> dict[str, Any]:
        return {
            "applications": {
                name: {
                    "status": self.status,
                    "message": self.message,
                    "deployed_app_config": spec,
                }
                for name, spec in self.apps.items()
            }
        }

    def put(self, applications: list[dict[str, Any]]) -> None:
        self.apps = {a["name"]: a for a in applications}


def _stub_build_spec(
    family: str, suffix: str, run_name: str, meta: BundleMetadata
) -> dict[str, Any]:
    return {
        "name": f"{family}__{suffix}__{run_name}",
        "route_prefix": f"/r/{family}/{suffix}/{run_name}",
        "import_path": "stub:Stub",
        "args": {"family": family, "suffix": suffix, "run_name": run_name},
        "runtime_env": {"working_dir": meta.bundle_url},
    }


class TestModelServing(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        patches = [
            patch(
                "cortexgrid.model_serving.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexgrid.model_serving.put_serve_applications",
                side_effect=self.state.put,
            ),
            patch(
                "cortexgrid.model_serving.get_ray_serve_uri",
                return_value="http://ray:30000",
            ),
            patch(
                "cortexgrid.model_serving._build_application_spec",
                side_effect=_stub_build_spec,
            ),
            patch(
                "cortexgrid.model_serving._load_bundle_metadata",
                return_value=_FAKE_META,
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_deployed_model_appears_in_listings(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")

        listed = list_deployed_models()

        self.assertEqual(
            [(d.family, d.suffix, d.run_name) for d in listed],
            [("Qwen2", "instruct", "boogey-46")],
        )

    def test_undeployed_model_disappears_from_listings(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        undeploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(list_deployed_models(), [])

    def test_deployment_url_combines_serve_uri_and_route(self) -> None:
        d = deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(d.url, "http://ray:30000/r/Qwen2/instruct/boogey-46")

    def test_apps_not_using_our_naming_scheme_excluded_from_listings(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        self.state.apps["unrelated-app"] = {"name": "unrelated-app"}
        self.state.apps["only__two"] = {"name": "only__two"}

        listed = list_deployed_models()

        self.assertEqual([d.family for d in listed], ["Qwen2"])

    def test_list_returns_empty_when_nothing_deployed(self) -> None:
        self.assertEqual(list_deployed_models(), [])

    def test_redeploying_same_triple_replaces_prior_spec(self) -> None:
        deploy_model("Qwen2", "instruct", "boogey-46")
        deploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(len(self.state.apps), 1)

    def test_deploy_model_with_wait_returns_deployment_pointing_at_app_url(self) -> None:
        self.state.status = "RUNNING"

        d = deploy_model("Qwen2", "instruct", "boogey-46", wait=True)

        self.assertEqual(d.url, "http://ray:30000/r/Qwen2/instruct/boogey-46")

    def test_deploy_model_with_wait_raises_on_deploy_failed(self) -> None:
        self.state.status = "DEPLOY_FAILED"
        self.state.message = "replica died on import"

        with patch("cortexgrid.model_serving.time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                deploy_model("Qwen2", "instruct", "boogey-46", wait=True)

        self.assertIn("DEPLOY_FAILED", str(ctx.exception))
        self.assertIn("replica died on import", str(ctx.exception))


class TestWaitForApplicationRunning(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        self.state.apps["Qwen2__instruct__boogey-46"] = {
            "name": "Qwen2__instruct__boogey-46"
        }
        patches = [
            patch(
                "cortexgrid.model_serving.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch("cortexgrid.model_serving.time.sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_times_out_when_status_never_reaches_running(self) -> None:
        self.state.status = "DEPLOYING"
        self.state.message = "still booting"

        with self.assertRaises(TimeoutError) as ctx:
            _wait_for_application_running(
                "Qwen2__instruct__boogey-46", timeout_s=0.05, interval_s=0.0
            )

        self.assertIn("DEPLOYING", str(ctx.exception))
        self.assertIn("still booting", str(ctx.exception))

    def test_treats_unhealthy_as_transient_until_timeout(self) -> None:
        self.state.status = "UNHEALTHY"

        with self.assertRaises(TimeoutError):
            _wait_for_application_running(
                "Qwen2__instruct__boogey-46", timeout_s=0.05, interval_s=0.0
            )

    def test_finite_timeout_elapsing_raises_timeout_error(self) -> None:
        self.state.status = "DEPLOYING"

        with self.assertRaises(TimeoutError):
            _wait_for_application_running(
                "Qwen2__instruct__boogey-46", timeout_s=0.05, interval_s=0.0
            )

    def test_unbounded_timeout_returns_once_status_reaches_running(self) -> None:
        statuses = ["DEPLOYING", "DEPLOYING", "RUNNING"]

        def get_details() -> dict[str, Any]:
            self.state.status = statuses.pop(0)
            return self.state.get_details()

        with patch(
            "cortexgrid.model_serving.get_serve_details", side_effect=get_details
        ):
            _wait_for_application_running(
                "Qwen2__instruct__boogey-46", timeout_s=None, interval_s=0.0
            )

        self.assertEqual(statuses, [])

    def test_deploy_failed_raises_regardless_of_unbounded_timeout(self) -> None:
        self.state.status = "DEPLOY_FAILED"
        self.state.message = "replica died on import"

        with self.assertRaises(RuntimeError) as ctx:
            _wait_for_application_running(
                "Qwen2__instruct__boogey-46", timeout_s=None, interval_s=0.0
            )

        self.assertIn("DEPLOY_FAILED", str(ctx.exception))


class TestModelServingStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        patches = [
            patch(
                "cortexgrid.model_serving.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexgrid.model_serving.get_ray_serve_uri",
                return_value="http://ray:30000",
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _serve_app(self, status: str, message: str = "") -> None:
        self.state.apps["fam__suf__run"] = {"name": "fam__suf__run"}
        self.state.status = status
        self.state.message = message

    def test_not_deployed_when_no_serve_app(self) -> None:
        s = model_serving_status("fam", "suf", "run")
        self.assertEqual(s.phase, "not_deployed")
        self.assertIsNone(s.url)

    def test_not_started_is_reported_distinctly(self) -> None:
        self._serve_app("NOT_STARTED")
        self.assertEqual(
            model_serving_status("fam", "suf", "run").phase, "not_started"
        )

    def test_deploying_reflects_controller_status_and_message(self) -> None:
        self._serve_app("DEPLOYING", "pulling weights")
        s = model_serving_status("fam", "suf", "run")
        self.assertEqual(s.phase, "deploying")
        self.assertEqual(s.message, "pulling weights")

    def test_running_carries_the_route_url(self) -> None:
        self._serve_app("RUNNING")
        s = model_serving_status("fam", "suf", "run")
        self.assertEqual(s.phase, "running")
        self.assertEqual(s.url, "http://ray:30000/r/fam/suf/run")

    def test_unhealthy_maps_to_unhealthy(self) -> None:
        self._serve_app("UNHEALTHY")
        self.assertEqual(
            model_serving_status("fam", "suf", "run").phase, "unhealthy"
        )

    def test_deleting_maps_to_deleting(self) -> None:
        self._serve_app("DELETING")
        self.assertEqual(
            model_serving_status("fam", "suf", "run").phase, "deleting"
        )

    def test_deploy_failed_maps_to_failed(self) -> None:
        self._serve_app("DEPLOY_FAILED", "oom")
        self.assertEqual(
            model_serving_status("fam", "suf", "run").phase, "failed"
        )


if __name__ == "__main__":
    unittest.main()
