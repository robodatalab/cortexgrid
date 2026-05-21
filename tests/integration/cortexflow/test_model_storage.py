from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cortexflow

from tests.integration.cortexflow._ray_run import (
    experiment_name,
    get_logger,
)

log = get_logger(__name__)


def _make_weights_dir() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "config.json").write_text('{"family": "fake", "n": 1}')
    (d / "model.safetensors").write_bytes(b"fake-weights-bytes")
    return d


def _matches(model: cortexflow.SavedModel, family: str, suffix: str, run_name: str) -> bool:
    return (
        model.family == family
        and model.suffix == suffix
        and model.run_name == run_name
    )


class TestServingStorage(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)
        self.name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, self.name)
        self.exp = cortexflow.Experiment.init(self.name)
        self.run_name = self.exp.run_name()

    def test_save_load_roundtrip(self) -> None:
        weights = _make_weights_dir()
        cortexflow.save_model(weights, suffix="instruct", family="ft-fake")

        loaded = cortexflow.load_model("ft-fake", "instruct", self.run_name)
        self.assertEqual(
            (loaded / "config.json").read_text(),
            '{"family": "fake", "n": 1}',
        )
        self.assertEqual(
            (loaded / "model.safetensors").read_bytes(),
            b"fake-weights-bytes",
        )

    def test_list_includes_saved_model(self) -> None:
        cortexflow.save_model(
            _make_weights_dir(), suffix="instruct", family="ft-fake"
        )
        models = cortexflow.list_models()
        self.assertTrue(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )

    def test_delete_model_removes_it(self) -> None:
        cortexflow.save_model(
            _make_weights_dir(), suffix="instruct", family="ft-fake"
        )
        cortexflow.delete_model("ft-fake", "instruct", self.run_name)
        models = cortexflow.list_models()
        self.assertFalse(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )

    def test_delete_experiment_cascades_models(self) -> None:
        cortexflow.save_model(
            _make_weights_dir(), suffix="instruct", family="ft-fake"
        )
        cortexflow.delete_experiment(self.name)
        models = cortexflow.list_models()
        self.assertFalse(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )

    def test_save_stamps_size_bytes_matching_uploaded_files(self) -> None:
        weights = _make_weights_dir()
        expected = sum(
            p.stat().st_size for p in weights.rglob("*") if p.is_file()
        )
        saved = cortexflow.save_model(weights, suffix="instruct", family="ft-fake")
        self.assertEqual(saved.size_bytes, expected)
        listed = next(
            m for m in cortexflow.list_models()
            if _matches(m, "ft-fake", "instruct", self.run_name)
        )
        self.assertEqual(listed.size_bytes, expected)


if __name__ == "__main__":
    unittest.main()
