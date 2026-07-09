from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cortexflow

from tests.integration.cortexflow._ray_run import experiment_name, get_logger
from tests.integration.stubs.serving import (
    AddConstantServeApp,
    read_constant,
    write_weights,
)

log = get_logger(__name__)


def _matches(
    model: cortexflow.SavedModel, family: str, suffix: str, run_name: str
) -> bool:
    return (
        model.family == family and model.suffix == suffix and model.run_name == run_name
    )


class TestServingStorage(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)
        self.name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, self.name)
        self.exp = cortexflow.Experiment.init(self.name)
        self.run_name = self.exp.run_name()

    def _save_stub(self, family: str, suffix: str, constant: int = 15) -> None:
        with tempfile.TemporaryDirectory() as d:
            write_weights(Path(d), constant)
            cortexflow.save_model(
                Path(d), AddConstantServeApp, family=family, suffix=suffix
            )

    def test_save_load_roundtrip(self) -> None:
        self._save_stub("ft-fake", "instruct", constant=15)

        restored = cortexflow.load_model("ft-fake", "instruct", self.run_name)
        self.assertIsInstance(restored, Path)
        self.assertEqual(read_constant(restored), 15)

    def test_list_includes_saved_model(self) -> None:
        self._save_stub("ft-fake", "instruct")

        models = cortexflow.list_models()
        self.assertTrue(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )

    def test_delete_model_removes_it(self) -> None:
        self._save_stub("ft-fake", "instruct")

        cortexflow.delete_model("ft-fake", "instruct", self.run_name)
        models = cortexflow.list_models()
        self.assertFalse(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )

    def test_delete_experiment_cascades_models(self) -> None:
        self._save_stub("ft-fake", "instruct")

        cortexflow.delete_experiment(self.name)
        models = cortexflow.list_models()
        self.assertFalse(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )


if __name__ == "__main__":
    unittest.main()
