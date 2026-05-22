from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cortexflow
from cortexflow.model import DeployedModel, Model


class TinyModel(Model):
    def __init__(self, constant: int) -> None:
        self.constant = constant

    def infer(self, x: int) -> int:
        return x + self.constant

    def save(self, d: Path) -> None:
        (d / "weights.json").write_text(json.dumps({"constant": self.constant}))

    @classmethod
    def load(cls, d: Path) -> "TinyModel":
        constant = json.loads((d / "weights.json").read_text())["constant"]
        return cls(constant=constant)


class TestModelAbstract(unittest.TestCase):
    def test_cannot_instantiate_without_implementing_all_methods(self) -> None:
        class Incomplete(Model):
            def infer(self, x):
                return x

        with self.assertRaises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_with_full_implementation_works_locally(self) -> None:
        m = TinyModel(constant=15)
        self.assertEqual(m.infer(3), 18)

    def test_save_and_load_round_trip_through_directory(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            TinyModel(constant=42).save(Path(d))
            self.assertEqual(TinyModel.load(Path(d)).constant, 42)


class TestDeployedModelProxy(unittest.TestCase):
    def test_infer_posts_args_and_kwargs_returns_unwrapped_result(self) -> None:
        proxy = DeployedModel(url="http://ray:30000/r/fam/sfx/run")
        fake_response = MagicMock()
        fake_response.json.return_value = {"result": 18}
        with patch(
            "cortexflow.model.requests.post", return_value=fake_response
        ) as post:
            result = proxy.infer(3, k="v")

        self.assertEqual(result, 18)
        post.assert_called_once_with(
            "http://ray:30000/r/fam/sfx/run/infer",
            json={"args": [3], "kwargs": {"k": "v"}},
            timeout=60,
        )

    def test_infer_raises_on_http_error(self) -> None:
        proxy = DeployedModel(url="http://ray/x")
        fake_response = MagicMock()
        fake_response.raise_for_status.side_effect = RuntimeError("boom")
        with patch("cortexflow.model.requests.post", return_value=fake_response):
            with self.assertRaises(RuntimeError):
                proxy.infer(1)


class TestSaveModelRejectsNonModel(unittest.TestCase):
    def test_typeerror_when_passed_a_plain_object(self) -> None:
        with self.assertRaises(TypeError):
            cortexflow.save_model(object(), family="x", suffix="y")


if __name__ == "__main__":
    unittest.main()
