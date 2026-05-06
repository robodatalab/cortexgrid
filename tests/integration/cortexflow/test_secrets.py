from __future__ import annotations

import unittest
import uuid

import cortexflow
from parameterized import parameterized  # type: ignore

from tests.integration.cortexflow._ray_run import (
    RUN_MODES,
    get_logger,
    run,
    experiment_name,
)

log = get_logger(__name__)


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


class TestSecrets(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)

    @parameterized.expand(RUN_MODES)
    def test_get_known_secret(self, mode) -> None:
        name = experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        run(mode=mode, log=log, fn=_get_known_secret)

    @parameterized.expand(RUN_MODES)
    def test_set_get_delete_roundtrip(self, mode) -> None:
        key = f"it-{uuid.uuid4().hex[:8]}-roundtrip"
        self.addCleanup(cortexflow.delete_secret, key)
        name = experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        run(mode=mode, log=log, fn=_set_get_delete_roundtrip, key=key)
