from __future__ import annotations

import unittest
import uuid

import cortexflow
from parameterized import parameterized  # type: ignore

from tests.integration.cortexflow._ray_run import schedule_and_wait


def _run_main(fn, *args, **kwargs):
    fn(*args, **kwargs)


def _get_known_secret() -> None:
    if not cortexflow.get_secret("S3_BUCKET_NAME"):
        raise RuntimeError("get_secret returned empty")


def _set_get_delete_roundtrip(key: str) -> None:
    cortexflow.set_secret(key, "hello")
    if cortexflow.get_secret(key) != "hello":
        raise RuntimeError("get returned wrong value")
    cortexflow.delete_secret(key)
    if key in cortexflow.list_secrets():
        raise RuntimeError("delete left the secret in place")


def _experiment_name() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


_RUNNERS = [
    ("main_process", _run_main),
    ("via_ray_job", schedule_and_wait),
]


class TestSecrets(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)

    @parameterized.expand(_RUNNERS)
    def test_get_known_secret(self, _mode, run) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        run(_get_known_secret)

    @parameterized.expand(_RUNNERS)
    def test_set_get_delete_roundtrip(self, _mode, run) -> None:
        key = f"it-{uuid.uuid4().hex[:8]}-roundtrip"
        self.addCleanup(cortexflow.delete_secret, key)
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        run(_set_get_delete_roundtrip, key)
