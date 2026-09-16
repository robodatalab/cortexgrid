from __future__ import annotations

import inspect
import unittest
from unittest.mock import MagicMock, patch

from cortexgrid import serve
from cortexgrid._serve_entry import build


_APP = object()


@serve.ingress(_APP)
class _MarkedServeApp:
    num_gpus = 1
    num_replicas = 2


class _UnmarkedServeApp:
    pass


_ARGS = {"family": "fam", "suffix": "suf", "run_name": "run"}


class TestIngress(unittest.TestCase):
    def test_class_stays_locatable_at_its_own_module(self) -> None:
        self.assertEqual(_MarkedServeApp.__module__, __name__)
        self.assertEqual(inspect.getfile(_MarkedServeApp), __file__)

    def test_records_the_app_on_the_class(self) -> None:
        self.assertIs(serve.ingress_app(_MarkedServeApp), _APP)

    def test_unmarked_class_has_no_app(self) -> None:
        self.assertIsNone(serve.ingress_app(_UnmarkedServeApp))


class TestBuild(unittest.TestCase):
    def test_applies_ray_ingress_with_the_marked_app(self) -> None:
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            build({**_ARGS, "class_import_path": f"{__name__}:_MarkedServeApp"})

        ray_serve.ingress.assert_called_once_with(_APP)
        (on_replica,) = ray_serve.ingress.return_value.call_args.args
        self.assertTrue(issubclass(on_replica, _MarkedServeApp))
        self.assertEqual(on_replica.__name__, _MarkedServeApp.__name__)
        wrapped = ray_serve.ingress.return_value.return_value
        ray_serve.deployment.assert_called_once_with(wrapped)

    def test_replica_instance_creation_reapplies_ray_ingress(self) -> None:
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            ray_serve.ingress.return_value.side_effect = lambda cls: cls
            build({**_ARGS, "class_import_path": f"{__name__}:_MarkedServeApp"})
            (on_replica,) = ray_serve.deployment.call_args.args
            ray_serve.ingress.reset_mock()

            instance = on_replica.__new__(on_replica)

        ray_serve.ingress.assert_called_once_with(_APP)
        ray_serve.ingress.return_value.assert_called_once_with(_MarkedServeApp)
        self.assertIsInstance(instance, _MarkedServeApp)

    def test_reads_resources_from_the_class(self) -> None:
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            ray_serve.ingress.return_value.side_effect = lambda cls: cls
            build({**_ARGS, "class_import_path": f"{__name__}:_MarkedServeApp"})

        ray_serve.deployment.return_value.options.assert_called_once_with(
            num_replicas=2,
            max_ongoing_requests=100,
            ray_actor_options={"num_gpus": 1},
        )

    def test_unmarked_class_is_deployed_without_ingress(self) -> None:
        with patch("cortexgrid._serve_entry.serve", MagicMock()) as ray_serve:
            build({**_ARGS, "class_import_path": f"{__name__}:_UnmarkedServeApp"})

        ray_serve.ingress.assert_not_called()
        ray_serve.deployment.assert_called_once_with(_UnmarkedServeApp)


if __name__ == "__main__":
    unittest.main()
