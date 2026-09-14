from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cortexgrid
from parameterized import parameterized  # type: ignore

from tests.integration.cortexgrid._ray_run import (
    RUN_MODES,
    get_logger,
    run,
    experiment_name,
)

log = get_logger(__name__)


def _s3_roundtrip(prefix: str, payload: bytes) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.bin"
        dst = Path(tmp) / "dst.bin"
        src.write_bytes(payload)
        cortexgrid.upload(str(src), f"{prefix}/file.bin")
        cortexgrid.download(f"{prefix}/file.bin", str(dst))
        if dst.read_bytes() != payload:
            raise RuntimeError("s3 roundtrip mismatch")


def _s3_dir_upload(prefix: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.txt").write_text("A")
        (Path(tmp) / "b.txt").write_text("B")
        cortexgrid.upload_dir(tmp, prefix)
        a_back = cortexgrid.download(f"{prefix}/a.txt", str(Path(tmp) / "a-back.txt"))
        b_back = cortexgrid.download(f"{prefix}/b.txt", str(Path(tmp) / "b-back.txt"))
        if Path(a_back).read_text() != "A":
            raise RuntimeError("a content wrong")
        if Path(b_back).read_text() != "B":
            raise RuntimeError("b content wrong")


class TestS3(unittest.TestCase):
    def setUp(self) -> None:
        cortexgrid.Experiment.close()
        self.addCleanup(cortexgrid.Experiment.close)

    @parameterized.expand(RUN_MODES)
    def test_upload_download_roundtrip(self, mode) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexgrid.delete_prefix, name)
        self.addCleanup(cortexgrid.delete_experiment, name)
        cortexgrid.Experiment.init(name)
        run(mode=mode, log=log, fn=_s3_roundtrip, prefix=name, payload=b"hello")

    @parameterized.expand(RUN_MODES)
    def test_upload_dir(self, mode) -> None:
        name = experiment_name(self)
        self.addCleanup(cortexgrid.delete_prefix, name)
        self.addCleanup(cortexgrid.delete_experiment, name)
        cortexgrid.Experiment.init(name)
        run(mode=mode, log=log, fn=_s3_dir_upload, prefix=name)
