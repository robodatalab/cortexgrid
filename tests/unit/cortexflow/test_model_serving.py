from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from cortexflow.model_serving import (
    deploy_model,
    list_deployed_models,
    model_deployment,
    undeploy_model,
)


class _FakeBinding:
    def __init__(self, cls: type, args: tuple) -> None:
        self.cls = cls
        self.args = args


class _FakeRayDeployment:
    """Stand-in for what `@serve.deployment(...)` wraps a class into."""

    def __init__(self, cls: type) -> None:
        self._cls = cls

    def bind(self, *args: Any) -> _FakeBinding:
        return _FakeBinding(self._cls, args)


class FakeServe:
    """In-memory Ray Serve stand-in. Tracks which app names are deployed."""

    def __init__(self) -> None:
        self.apps: dict[str, str] = {}

    def deployment(self, **_: Any) -> Any:
        def wrap(cls: type) -> _FakeRayDeployment:
            return _FakeRayDeployment(cls)

        return wrap

    def run(self, app: _FakeBinding, *, name: str, route_prefix: str) -> None:
        self.apps[name] = "RUNNING"

    def delete(self, name: str) -> None:
        self.apps.pop(name, None)

    def status(self) -> Any:
        return SimpleNamespace(
            applications={n: SimpleNamespace(status=s) for n, s in self.apps.items()}
        )


def _patches(serve: FakeServe) -> list:
    return [
        patch("cortexflow.model_serving.ray.is_initialized", return_value=True),
        patch("cortexflow.model_serving.serve", serve),
        patch(
            "cortexflow.model_serving.get_ray_serve_uri",
            return_value="http://ray:30000",
        ),
    ]


@model_deployment(num_gpus=0)
class _TestableStub:
    def __init__(self, family: str, suffix: str, run_name: str) -> None:
        self._info = (family, suffix, run_name)


class TestModelServing(unittest.TestCase):
    def setUp(self) -> None:
        self.serve = FakeServe()
        for p in _patches(self.serve):
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

    def test_apps_not_using_our_naming_scheme_excluded_from_listings(
        self,
    ) -> None:
        deploy_model(_TestableStub, "Qwen2", "instruct", "boogey-46")
        self.serve.apps["unrelated-app"] = "RUNNING"
        self.serve.apps["only__two"] = "RUNNING"

        listed = list_deployed_models()

        self.assertEqual([d.family for d in listed], ["Qwen2"])

    def test_list_returns_empty_when_nothing_deployed(self) -> None:
        self.assertEqual(list_deployed_models(), [])


if __name__ == "__main__":
    unittest.main()
