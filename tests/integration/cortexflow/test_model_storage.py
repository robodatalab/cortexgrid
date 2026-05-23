from __future__ import annotations

from typing import cast
import unittest

import cortexflow

from tests.integration.cortexflow._ray_run import experiment_name, get_logger
from tests.integration.stubs.serving import AddConstantModel

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

    def test_save_load_roundtrip(self) -> None:
        cortexflow.save_model(
            AddConstantModel(constant=15), family="ft-fake", suffix="instruct"
        )

        restored = cortexflow.load_model("ft-fake", "instruct", self.run_name)
        self.assertIsInstance(restored, AddConstantModel)

        restored_typed = cast(AddConstantModel, restored)
        self.assertEqual(restored_typed.constant, 15)
        self.assertEqual(restored_typed.infer(27), 42)

    def test_list_includes_saved_model(self) -> None:
        cortexflow.save_model(
            AddConstantModel(constant=15), family="ft-fake", suffix="instruct"
        )

        models = cortexflow.list_models()
        self.assertTrue(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )

    def test_delete_model_removes_it(self) -> None:
        cortexflow.save_model(
            AddConstantModel(constant=15), family="ft-fake", suffix="instruct"
        )

        cortexflow.delete_model("ft-fake", "instruct", self.run_name)
        models = cortexflow.list_models()
        self.assertFalse(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )

    def test_delete_experiment_cascades_models(self) -> None:
        cortexflow.save_model(
            AddConstantModel(constant=15), family="ft-fake", suffix="instruct"
        )

        cortexflow.delete_experiment(self.name)
        models = cortexflow.list_models()
        self.assertFalse(
            any(_matches(m, "ft-fake", "instruct", self.run_name) for m in models)
        )


if __name__ == "__main__":
    unittest.main()
