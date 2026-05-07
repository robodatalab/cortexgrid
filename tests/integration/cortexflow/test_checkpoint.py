from __future__ import annotations

import unittest

import cortexflow
from parameterized import parameterized  # type: ignore

from tests.integration.cortexflow._ray_run import (
    RUN_MODES,
    get_logger,
    run,
    experiment_name,
)

log = get_logger(__name__)


def _run_crash_first_run() -> None:
    old_ckpt = cortexflow.resume()
    log.info("Checkpoint %s", "doesn't yet exist" if old_ckpt is None else "loaded")

    is_this_first_run = old_ckpt is None

    with cortexflow.checkpoint() as new_ckpt:
        log.info("Creating a checkpoint")
        new_ckpt.epoch = 1

    if is_this_first_run:
        log.info("Simulating a crash...")
        raise RuntimeError("simulated crash on first run")
    else:
        log.info(
            "Checkpointed value (should == 1): %d",
            old_ckpt.epoch if old_ckpt is not None else -1,
        )

    log.info("Proceeding on a non-crash path")

    cortexflow.log_metric("experiment_finished", 2)


class TestCheckpoint(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)

    @parameterized.expand(RUN_MODES)
    def test_resumes_after_crash(self, mode) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run(mode=mode, log=log, fn=_run_crash_first_run, retry=True)
        experiment_finished_value = cortexflow.get_metric_history(
            exp.run_id, "experiment_finished"
        )
        self.assertEqual([p["value"] for p in experiment_finished_value], [2])
