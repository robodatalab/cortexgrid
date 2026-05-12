from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from cortexflow.model_serving import (
    deploy_model,
    list_deployed_models,
    model_deployment,
    undeploy_model,
)


class FakeServeState:
    """In-memory stand-in for Ray Serve's declarative app registry."""

    def __init__(self) -> None:
        self.apps: dict[str, dict[str, Any]] = {}

    def get_details(self) -> dict[str, Any]:
        return {
            "applications": {
                name: {"status": "RUNNING", "deployed_app_config": spec}
                for name, spec in self.apps.items()
            }
        }

    def put(self, applications: list[dict[str, Any]]) -> None:
        self.apps = {a["name"]: a for a in applications}


@model_deployment(num_gpus=0)
class _TestableStub:
    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        self._info = (family, suffix, run_name)


def _stub_build_spec(
    cls: Any, family: str, suffix: str, run_name: str
) -> dict[str, Any]:
    return {
        "name": f"{family}__{suffix}__{run_name}",
        "route_prefix": f"/r/{family}/{suffix}/{run_name}",
        "import_path": "stub:Stub",
        "args": {"family": family, "suffix": suffix, "run_name": run_name},
        "runtime_env": {"working_dir": "s3://bucket/stub.zip", "pip": []},
    }


class TestModelServing(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeServeState()
        patches = [
            patch(
                "cortexflow.model_serving.get_serve_details",
                side_effect=self.state.get_details,
            ),
            patch(
                "cortexflow.model_serving.put_serve_applications",
                side_effect=self.state.put,
            ),
            patch(
                "cortexflow.model_serving.get_ray_serve_uri",
                return_value="http://ray:30000",
            ),
            patch(
                "cortexflow.model_serving._build_application_spec",
                side_effect=_stub_build_spec,
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_deployed_model_appears_in_listings(self) -> None:
        deploy_model(_TestableStub, "Qwen2", "instruct", "boogey-46")

        listed = list_deployed_models()

        self.assertEqual(
            [(d.family, d.suffix, d.run_name) for d in listed],
            [("Qwen2", "instruct", "boogey-46")],
        )

    def test_undeployed_model_disappears_from_listings(self) -> None:
        deploy_model(_TestableStub, "Qwen2", "instruct", "boogey-46")
        undeploy_model("Qwen2", "instruct", "boogey-46")

        self.assertEqual(list_deployed_models(), [])

    def test_deployment_url_combines_serve_uri_and_route(self) -> None:
        d = deploy_model(_TestableStub, "Qwen2", "instruct", "boogey-46")

        self.assertEqual(d.url, "http://ray:30000/r/Qwen2/instruct/boogey-46")

    def test_apps_not_using_our_naming_scheme_excluded_from_listings(self) -> None:
        deploy_model(_TestableStub, "Qwen2", "instruct", "boogey-46")
        self.state.apps["unrelated-app"] = {"name": "unrelated-app"}
        self.state.apps["only__two"] = {"name": "only__two"}

        listed = list_deployed_models()

        self.assertEqual([d.family for d in listed], ["Qwen2"])

    def test_list_returns_empty_when_nothing_deployed(self) -> None:
        self.assertEqual(list_deployed_models(), [])

    def test_redeploying_same_triple_replaces_prior_spec(self) -> None:
        deploy_model(_TestableStub, "Qwen2", "instruct", "boogey-46")
        deploy_model(_TestableStub, "Qwen2", "instruct", "boogey-46")

        self.assertEqual(len(self.state.apps), 1)


if __name__ == "__main__":
    unittest.main()
