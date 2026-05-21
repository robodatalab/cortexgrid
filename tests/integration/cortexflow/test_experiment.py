from __future__ import annotations

import unittest

import cortexflow
from cortexflow.experiment import get_experiment_by_run_name
from parameterized import parameterized  # type: ignore

from tests.integration.cortexflow._ray_run import (
    RUN_MODES,
    get_logger,
    run,
    experiment_name,
)

log = get_logger(__name__)


def _log_params_dict() -> None:
    cortexflow.log_params({"lr": "0.01"})


def _log_metrics_dict() -> None:
    cortexflow.log_metrics({"loss": 0.5, "accuracy": 0.9, "lr": 0.01})


def _log_metric_history() -> None:
    cortexflow.log_metric("loss", 1.0, step=0)
    cortexflow.log_metric("loss", 0.5, step=1)
    cortexflow.log_metric("loss", 0.25, step=2)


class TestExperiment(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)

    def test_init_creates_experiment_visible_in_listings(self) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        self.assertIn(name, [e.experiment_name for e in cortexflow.list_experiments()])

    @parameterized.expand(RUN_MODES)
    def test_log_params(self, mode) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run(mode=mode, log=log, fn=_log_params_dict)
        self.assertEqual(cortexflow.list_run_params(exp.run_id).get("lr"), "0.01")

    @parameterized.expand(RUN_MODES)
    def test_log_metrics_dict(self, mode) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run(mode=mode, log=log, fn=_log_metrics_dict)
        metrics = cortexflow.list_run_metrics(exp.run_id)
        self.assertIn("loss", metrics)
        self.assertIn("accuracy", metrics)
        self.assertIn("lr", metrics)

    @parameterized.expand(RUN_MODES)
    def test_log_metric_history(self, mode) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run(mode=mode, log=log, fn=_log_metric_history)
        points = sorted(
            cortexflow.get_metric_history(exp.run_id, "loss"), key=lambda p: p["step"]
        )
        self.assertEqual(
            [(p["step"], p["value"]) for p in points],
            [(0, 1.0), (1, 0.5), (2, 0.25)],
        )

    def test_get_experiment_by_run_name_returns_matching_pair(self) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        resolved = get_experiment_by_run_name(exp.run_name())
        self.assertEqual(resolved.experiment_name, name)
        self.assertEqual(resolved.run_id, exp.run_id)

    def test_get_experiment_by_run_name_raises_on_unknown_name(self) -> None:
        with self.assertRaises(ValueError):
            get_experiment_by_run_name("nonexistent-run-name-zzz")

    def test_get_experiment_by_run_name_picks_correct_experiment_when_multiple_exist(
        self,
    ) -> None:
        name_a = experiment_name(self) + "-a"
        name_b = experiment_name(self) + "-b"
        self.addCleanup(cortexflow.delete_experiment, name_a)
        self.addCleanup(cortexflow.delete_experiment, name_b)
        exp_a = cortexflow.Experiment.init(name_a)
        run_name_a = exp_a.run_name()
        cortexflow.Experiment.close()
        cortexflow.Experiment.init(name_b)
        resolved = get_experiment_by_run_name(run_name_a)
        self.assertEqual(resolved.experiment_name, name_a)
        self.assertEqual(resolved.run_id, exp_a.run_id)
