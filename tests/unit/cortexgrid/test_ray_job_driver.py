"""The cluster-side driver: run the job's function, record what it produced.

The driver is the only writer of `job/{job_id}/result.pkl`. Recording is best
effort — a job that ran must not be reported as failed because its outcome
could not be uploaded — but it never swallows the job's own exception: Ray
derives the job's state from the driver's exit code.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloudpickle  # type: ignore

from cortexgrid._ray_job_driver import main
from cortexgrid.jobs import JobResult, Payload


EXPERIMENT_NAME = "exp"
RUN_ID = "run-1"


def _returns(value: object) -> object:
    return value


def _raises() -> None:
    raise ValueError("bad batch")


class TestRayJobDriver(unittest.TestCase):
    def setUp(self) -> None:
        self.recorded: list[JobResult] = []
        patchers = [
            patch("cortexgrid._ray_job_driver.Experiment"),
            patch("cortexgrid._ray_job_driver.set_cortexgrid_job_id"),
            patch.object(
                JobResult,
                "save_to_mlflow",
                autospec=True,
                side_effect=lambda result: self.recorded.append(result),
            ),
        ]
        self.mocks = [p.start() for p in patchers]
        for p in patchers:
            self.addCleanup(p.stop)
        self.save = self.mocks[-1]
        self.tmp = Path(tempfile.mkdtemp())

    def _payload_file(self, fn, *args) -> str:
        payload = Payload(
            experiment_name=EXPERIMENT_NAME,
            run_id=RUN_ID,
            job_id="job-1",
            fn=fn,
            args=args,
            kwargs={},
            project_code_root=str(self.tmp),
        )
        path = self.tmp / "payload.pkl"
        path.write_bytes(cloudpickle.dumps(payload))
        return str(path)

    def test_records_the_functions_return_value(self) -> None:
        main(self._payload_file(_returns, {"loss": 0.5}))

        self.assertEqual(len(self.recorded), 1)
        self.assertEqual(self.recorded[0].job_id, "job-1")
        self.assertEqual(self.recorded[0].unwrap(), {"loss": 0.5})

    def test_records_the_exception_and_still_fails_the_job(self) -> None:
        with self.assertRaises(ValueError):
            main(self._payload_file(_raises))

        self.assertEqual(len(self.recorded), 1)
        self.assertFalse(self.recorded[0].ok)
        self.assertIn("ValueError: bad batch", self.recorded[0].traceback or "")

    def test_a_failed_recording_does_not_fail_a_job_that_ran(self) -> None:
        self.save.side_effect = RuntimeError("mlflow down")

        main(self._payload_file(_returns, 1))  # must not raise

    def test_a_failed_recording_does_not_mask_the_jobs_exception(self) -> None:
        self.save.side_effect = RuntimeError("mlflow down")

        with self.assertRaises(ValueError):
            main(self._payload_file(_raises))

    def test_missing_payload_is_an_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            main(str(self.tmp / "nope.pkl"))
