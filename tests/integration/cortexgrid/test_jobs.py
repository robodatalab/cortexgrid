"""Blocking job results against the real cluster.

`remote` hands back a JobFuture; `result()` waits for the control plane to
dispatch the job, for Ray to run it, and then returns whatever the function
returned on the DGX — or raises whatever it raised there.
"""

from __future__ import annotations

import unittest

import cortexgrid

from tests.integration.cortexgrid._ray_run import experiment_name, get_logger

log = get_logger(__name__)

# The cluster has to pull an image, build a runtime env and schedule the job;
# the control plane polls every 5s on top of that.
JOB_TIMEOUT_SECONDS = 600


class JobBoom(RuntimeError):
    """Raised on the DGX, expected to arrive back here with its type intact."""


def _returns_a_value(scale: float) -> dict[str, float]:
    return {"scaled": 21.0 * scale}


def _returns_none() -> None:
    return None


def _raises() -> None:
    raise JobBoom("boom from the DGX")


class TestJobResults(unittest.TestCase):
    def setUp(self) -> None:
        cortexgrid.Experiment.close()
        self.addCleanup(cortexgrid.Experiment.close)
        name = experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, name)
        cortexgrid.Experiment.init(name)

    def test_result_returns_what_the_function_returned(self) -> None:
        job = cortexgrid.remote(_returns_a_value, 2.0)

        self.assertEqual(
            job.result(timeout=JOB_TIMEOUT_SECONDS), {"scaled": 42.0}
        )

    def test_result_returns_none_for_a_function_that_returns_nothing(self) -> None:
        job = cortexgrid.remote(_returns_none)

        self.assertIsNone(job.result(timeout=JOB_TIMEOUT_SECONDS))

    def test_result_raises_what_the_function_raised(self) -> None:
        job = cortexgrid.remote(_raises)

        with self.assertRaises(JobBoom) as caught:
            job.result(timeout=JOB_TIMEOUT_SECONDS)
        self.assertEqual(str(caught.exception), "boom from the DGX")
        # The remote traceback rides along as the chained cause.
        self.assertIsInstance(caught.exception.__cause__, cortexgrid.JobFailed)
        self.assertIn("JobBoom", str(caught.exception.__cause__))

    def test_get_job_result_collects_by_job_id(self) -> None:
        job = cortexgrid.remote(_returns_a_value, 1.0)

        result = cortexgrid.get_job_result(job.job_id, timeout=JOB_TIMEOUT_SECONDS)

        self.assertEqual(result, {"scaled": 21.0})

    def test_waiting_on_a_retry_job_is_rejected(self) -> None:
        job = cortexgrid.remote(_returns_a_value, 1.0, retry=True)
        self.addCleanup(
            cortexgrid.stop_experiment_run_jobs,
            cortexgrid.Experiment.get_instance().run_id,
        )

        with self.assertRaises(ValueError):
            job.result(timeout=JOB_TIMEOUT_SECONDS)
