from __future__ import annotations

import unittest
import uuid

import cortexflow
from parameterized import parameterized  # type: ignore

from tests.integration.cortexflow._ray_run import schedule_and_wait


def _train_loop_crash_first_run() -> None:
    ckpt = cortexflow.resume()
    start_epoch = (ckpt.epoch + 1) if ckpt else 0
    for epoch in range(start_epoch, 5):
        if epoch == 3 and start_epoch == 0:
            raise RuntimeError("simulated crash on first run")
        with cortexflow.checkpoint() as new_ckpt:
            new_ckpt.epoch = epoch
    cortexflow.log_metric("final_epoch", 4.0)
    cortexflow.log_metric("started_at_epoch", float(start_epoch))


def _experiment_name() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


def _run_main_crash_then_resume(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except RuntimeError:
        pass
    fn(*args, **kwargs)


def _run_ray_with_retry(fn, *args, **kwargs):
    schedule_and_wait(fn, *args, retry=True, **kwargs)


_RUNNERS = [
    ("main_process", _run_main_crash_then_resume),
    ("via_ray_job", _run_ray_with_retry),
]


class TestCheckpoint(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)

    @parameterized.expand(_RUNNERS)
    def test_resumes_after_crash(self, _mode, run) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run(_train_loop_crash_first_run)
        started = cortexflow.get_metric_history(exp.run_id, "started_at_epoch")
        final = cortexflow.get_metric_history(exp.run_id, "final_epoch")
        self.assertEqual([p["value"] for p in started], [3.0])
        self.assertEqual([p["value"] for p in final], [4.0])
