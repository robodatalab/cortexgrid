from __future__ import annotations

import unittest
import uuid

import cortexflow
from parameterized import parameterized  # type: ignore

from tests.integration.cortexflow._ray_run import schedule_and_wait


def _run_main(fn, *args, **kwargs):
    fn(*args, **kwargs)


def _log_params_dict() -> None:
    cortexflow.log_params({"lr": "0.01"})


def _log_metrics_dict() -> None:
    cortexflow.log_metrics({"loss": 0.5, "accuracy": 0.9, "lr": 0.01})


def _log_metric_history() -> None:
    cortexflow.log_metric("loss", 1.0, step=0)
    cortexflow.log_metric("loss", 0.5, step=1)
    cortexflow.log_metric("loss", 0.25, step=2)


def _experiment_name() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


_RUNNERS = [
    ("main_process", _run_main),
    ("via_ray_job", schedule_and_wait),
]


class TestExperiment(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)

    def test_init_creates_experiment_visible_in_listings(self) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        self.assertIn(name, [e.experiment_name for e in cortexflow.list_experiments()])

    @parameterized.expand(_RUNNERS)
    def test_log_params(self, _mode, run) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run(_log_params_dict)
        self.assertEqual(cortexflow.list_run_params(exp.run_id).get("lr"), "0.01")

    @parameterized.expand(_RUNNERS)
    def test_log_metrics_dict(self, _mode, run) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run(_log_metrics_dict)
        metrics = cortexflow.list_run_metrics(exp.run_id)
        self.assertIn("loss", metrics)
        self.assertIn("accuracy", metrics)
        self.assertIn("lr", metrics)

    @parameterized.expand(_RUNNERS)
    def test_log_metric_history(self, _mode, run) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run(_log_metric_history)
        points = sorted(
            cortexflow.get_metric_history(exp.run_id, "loss"), key=lambda p: p["step"]
        )
        self.assertEqual(
            [(p["step"], p["value"]) for p in points],
            [(0, 1.0), (1, 0.5), (2, 0.25)],
        )
