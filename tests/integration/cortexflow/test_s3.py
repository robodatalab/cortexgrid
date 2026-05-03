from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

import cortexflow
from parameterized import parameterized  # type: ignore

from tests.integration.cortexflow._ray_run import schedule_and_wait


def _run_main(fn, *args, **kwargs):
    fn(*args, **kwargs)


def _s3_roundtrip(prefix: str, payload: bytes) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.bin"
        dst = Path(tmp) / "dst.bin"
        src.write_bytes(payload)
        cortexflow.upload(str(src), f"{prefix}/file.bin")
        cortexflow.download(f"{prefix}/file.bin", str(dst))
        if dst.read_bytes() != payload:
            raise RuntimeError("s3 roundtrip mismatch")


def _s3_dir_upload(prefix: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.txt").write_text("A")
        (Path(tmp) / "b.txt").write_text("B")
        cortexflow.upload_dir(tmp, prefix)
        a_back = cortexflow.download(f"{prefix}/a.txt", str(Path(tmp) / "a-back.txt"))
        b_back = cortexflow.download(f"{prefix}/b.txt", str(Path(tmp) / "b-back.txt"))
        if Path(a_back).read_text() != "A":
            raise RuntimeError("a content wrong")
        if Path(b_back).read_text() != "B":
            raise RuntimeError("b content wrong")


def _experiment_name() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


def _prefix() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


_RUNNERS = [
    ("main_process", _run_main),
    ("via_ray_job", schedule_and_wait),
]


class TestS3(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)

    @parameterized.expand(_RUNNERS)
    def test_upload_download_roundtrip(self, _mode, run) -> None:
        prefix = _prefix()
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_prefix, prefix)
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        run(_s3_roundtrip, prefix, b"hello")

    @parameterized.expand(_RUNNERS)
    def test_upload_dir(self, _mode, run) -> None:
        prefix = _prefix()
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_prefix, prefix)
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        run(_s3_dir_upload, prefix)
