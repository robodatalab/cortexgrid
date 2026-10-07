from __future__ import annotations

import asyncio
import inspect
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from parameterized import parameterized
from ray import serve as ray_serve

from cortexgrid import serve
from cortexgrid._serve_entry import build
from cortexgrid._model_scheduler import model_autoscaling_config


@serve.ingress
class _MarkedServeApp:
    pass


class _UnmarkedServeApp:
    pass


@dataclass
class _Point:
    x: float
    y: float


_ARGS = {"family": "fam", "suffix": "suf", "run_name": "run"}


class TestIngress(unittest.TestCase):
    def test_class_stays_locatable_at_its_own_module(self) -> None:
        self.assertEqual(_MarkedServeApp.__module__, __name__)
        self.assertEqual(inspect.getfile(_MarkedServeApp), __file__)

    def test_records_a_fastapi_app_on_the_class(self) -> None:
        self.assertIsInstance(serve.ingress_app(_MarkedServeApp), FastAPI)

    def test_unmarked_class_has_no_app(self) -> None:
        self.assertIsNone(serve.ingress_app(_UnmarkedServeApp))


class TestEndpoint(unittest.TestCase):
    @parameterized.expand([
        ("parameters_from_the_body", "/add", {"x": 2, "y": 3}, 5),
        ("parameter_left_out_takes_its_default", "/add", {"x": 2}, 3),
        ("dataclass_in_and_out", "/mirror", {"point": {"x": 1.0, "y": 2.0}}, {"x": 2.0, "y": 1.0}),
        ("list_in_and_out", "/halve", {"xs": [2.0, 4.0]}, [1.0, 2.0]),
        ("empty_list", "/halve", {"xs": []}, []),
    ])
    def test_answers_post_at_the_method_name(
        self, _case: str, path: str, body: dict, expected: object
    ) -> None:
        @serve.ingress
        class _ServeApp:
            @serve.endpoint
            async def add(self, x: int, y: int = 1) -> int:
                return x + y

            @serve.endpoint
            async def mirror(self, point: _Point) -> _Point:
                return _Point(x=point.y, y=point.x)

            @serve.endpoint
            def halve(self, xs: list[float]) -> list[float]:
                return [x / 2 for x in xs]

        app = serve.ingress_app(_ServeApp)
        ray_ingress = ray_serve.ingress(app)
        ray_ingress(_ServeApp)
        replica_context = MagicMock(servable_object=_ServeApp())
        client = TestClient(app)
        with patch("ray.serve.get_replica_context", return_value=replica_context):
            response = client.post(path, json=body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)

    def test_method_not_marked_as_endpoint_is_not_served(self) -> None:
        @serve.ingress
        class _ServeApp:
            async def add(self, x: int) -> int:
                return x + 1

        app = serve.ingress_app(_ServeApp)
        client = TestClient(app)
        response = client.post("/add", json={"x": 1})

        self.assertEqual(response.status_code, 404)


class TestEndpointClient(unittest.TestCase):
    @parameterized.expand([
        ("parameters_by_position", "add", (2, 3), {}, 5),
        ("parameters_by_name", "add", (), {"x": 2, "y": 3}, 5),
        ("parameter_left_out_takes_its_default", "add", (2,), {}, 3),
        ("dataclass_in_and_out", "mirror", (_Point(x=1.0, y=2.0),), {}, _Point(x=2.0, y=1.0)),
    ])
    def test_async_endpoint_answers_over_http(
        self, _case: str, endpoint: str, args: tuple, kwargs: dict, expected: object
    ) -> None:
        @serve.ingress
        class _ServeApp:
            @serve.endpoint
            async def add(self, x: int, y: int = 1) -> int:
                return x + y

            @serve.endpoint
            async def mirror(self, point: _Point) -> _Point:
                return _Point(x=point.y, y=point.x)

        app = serve.ingress_app(_ServeApp)
        ray_ingress = ray_serve.ingress(app)
        ray_ingress(_ServeApp)
        replica_context = MagicMock(servable_object=_ServeApp())
        transport = httpx.ASGITransport(app=app)
        http_client = httpx.AsyncClient(transport=transport)
        deployment = MagicMock(url="http://serve-app")
        client = _ServeApp.client(deployment)
        calling = getattr(client, endpoint)
        with (
            patch("ray.serve.get_replica_context", return_value=replica_context),
            patch("cortexgrid.serve.httpx.AsyncClient", return_value=http_client),
        ):
            answering = calling(*args, **kwargs)
            answer = asyncio.run(answering)

        self.assertEqual(answer, expected)

    @parameterized.expand([
        ("list", [2.0, 4.0], [1.0, 2.0]),
        ("empty_list", [], []),
    ])
    def test_sync_endpoint_answers_over_http(
        self, _case: str, xs: list[float], expected: list[float]
    ) -> None:
        @serve.ingress
        class _ServeApp:
            @serve.endpoint
            def halve(self, xs: list[float]) -> list[float]:
                return [x / 2 for x in xs]

        app = serve.ingress_app(_ServeApp)
        ray_ingress = ray_serve.ingress(app)
        ray_ingress(_ServeApp)
        replica_context = MagicMock(servable_object=_ServeApp())
        http_client = TestClient(app)
        deployment = MagicMock(url="http://serve-app")
        client = _ServeApp.client(deployment)
        with (
            patch("ray.serve.get_replica_context", return_value=replica_context),
            patch("cortexgrid.serve.httpx.Client", return_value=http_client),
        ):
            answer = client.halve(xs)

        self.assertEqual(answer, expected)

    def test_client_has_none_of_the_serve_apps_own_code(self) -> None:
        @serve.ingress
        class _ServeApp:
            def __init__(self) -> None:
                self.loaded = True

            @classmethod
            def deploy(cls) -> None:
                pass

            def helper(self) -> None:
                pass

            @serve.endpoint
            async def add(self, x: int) -> int:
                return x + 1

        deployment = MagicMock(url="http://serve-app")
        client = _ServeApp.client(deployment)

        self.assertNotIsInstance(client, _ServeApp)
        self.assertFalse(hasattr(client, "loaded"))
        self.assertFalse(hasattr(client, "deploy"))
        self.assertFalse(hasattr(client, "helper"))
        self.assertTrue(hasattr(client, "add"))


class TestBuild(unittest.TestCase):
    def test_applies_ray_ingress_with_the_marked_app(self) -> None:
        app = serve.ingress_app(_MarkedServeApp)
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            build({**_ARGS, "class_import_path": f"{__name__}:_MarkedServeApp"})

        ray_serve.ingress.assert_called_once_with(app)
        (on_replica,) = ray_serve.ingress.return_value.call_args.args
        self.assertTrue(issubclass(on_replica, _MarkedServeApp))
        self.assertEqual(on_replica.__name__, _MarkedServeApp.__name__)
        wrapped = ray_serve.ingress.return_value.return_value
        ray_serve.deployment.assert_called_once_with(wrapped)

    def test_replica_instance_creation_reapplies_ray_ingress(self) -> None:
        app = serve.ingress_app(_MarkedServeApp)
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            ray_serve.ingress.return_value.side_effect = lambda cls: cls
            build({**_ARGS, "class_import_path": f"{__name__}:_MarkedServeApp"})
            (on_replica,) = ray_serve.deployment.call_args.args
            ray_serve.ingress.reset_mock()

            instance = on_replica.__new__(on_replica)

        ray_serve.ingress.assert_called_once_with(app)
        ray_serve.ingress.return_value.assert_called_once_with(_MarkedServeApp)
        self.assertIsInstance(instance, _MarkedServeApp)

    def test_applies_resources_from_the_args(self) -> None:
        actor_options = {"num_gpus": 1, "memory": 1024, "resources": {"vram_mib": 8192}}
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            build({
                **_ARGS,
                "class_import_path": f"{__name__}:_MarkedServeApp",
                "num_replicas": 2,
                "ray_actor_options": actor_options,
            })

        ray_serve.deployment.return_value.options.assert_called_once_with(
            autoscaling_config=model_autoscaling_config(2, []),
            max_ongoing_requests=100,
            ray_actor_options=actor_options,
        )

    def test_args_from_an_older_deployer_request_no_resources(self) -> None:
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            build({**_ARGS, "class_import_path": f"{__name__}:_MarkedServeApp"})

        ray_serve.deployment.return_value.options.assert_called_once_with(
            autoscaling_config=model_autoscaling_config(1, []),
            max_ongoing_requests=100,
            ray_actor_options={},
        )

    def test_tells_the_scheduler_which_apps_the_model_requires(self) -> None:
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            build({
                **_ARGS,
                "class_import_path": f"{__name__}:_MarkedServeApp",
                "required_apps": ["base"],
            })

        options = ray_serve.deployment.return_value.options.call_args.kwargs
        self.assertEqual(
            options["autoscaling_config"], model_autoscaling_config(1, ["base"])
        )

    def test_unmarked_class_is_deployed_without_ingress(self) -> None:
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            build({**_ARGS, "class_import_path": f"{__name__}:_UnmarkedServeApp"})

        ray_serve.ingress.assert_not_called()
        ray_serve.deployment.assert_called_once_with(_UnmarkedServeApp)


if __name__ == "__main__":
    unittest.main()
