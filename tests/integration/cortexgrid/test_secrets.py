from __future__ import annotations

import unittest
import uuid

import cortexgrid
from parameterized import parameterized  # type: ignore

from tests.integration.cortexgrid._ray_run import (
    RUN_MODES,
    get_logger,
    run,
    experiment_name,
)

log = get_logger(__name__)


def _get_known_secret() -> None:
    if not cortexgrid.get_secret("S3_BUCKET_NAME"):
        raise RuntimeError("get_secret returned empty")


def _set_get_delete_roundtrip(key: str) -> None:
    cortexgrid.set_secret(key, "hello")
    if cortexgrid.get_secret(key) != "hello":
        raise RuntimeError("get returned wrong value")
    cortexgrid.delete_secret(key)
    if key in cortexgrid.list_secrets():
        raise RuntimeError("delete left the secret in place")


class TestSecrets(unittest.TestCase):
    def setUp(self) -> None:
        cortexgrid.Experiment.close()
        self.addCleanup(cortexgrid.Experiment.close)

    @parameterized.expand(RUN_MODES)
    def test_get_known_secret(self, mode) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, name)
        cortexgrid.Experiment.init(name)
        run(mode=mode, log=log, fn=_get_known_secret)

    @parameterized.expand(RUN_MODES)
    def test_set_get_delete_roundtrip(self, mode) -> None:
        key = f"it-{uuid.uuid4().hex[:8]}-roundtrip"
        self.addCleanup(cortexgrid.delete_secret, key)
        name = experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, name)
        cortexgrid.Experiment.init(name)
        run(mode=mode, log=log, fn=_set_get_delete_roundtrip, key=key)
