from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from cortexgrid.checkpoint import (
    Checkpoint,
    _checkpoint_prefix,
    checkpoint,
    resume,
    set_cortexgrid_job_id,
)
from cortexgrid.experiment import Experiment, clear_instance, set_instance
from tests.fakes import FakeState


def _make_experiment() -> Experiment:
    return Experiment(
        experiment_name="exp",
        run_id="run-1",
    )


class FakeS3:
    """Fake s3_util backed by a temp directory."""

    BUCKET = "canonical"

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def upload(self, local_path: str, dest_path: str | None = None) -> str:
        dest_path = dest_path or Path(local_path).name
        dest = self.root / self.BUCKET / dest_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)
        return f"s3://{self.BUCKET}/{dest_path}"

    def download(self, src_path: str, local_path: str | None = None) -> str:
        local_path = local_path or Path(src_path).name
        shutil.copy2(self.root / self.BUCKET / src_path, local_path)
        return local_path


class TestCheckpointPrefix(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()
        set_instance(_make_experiment())
        self.fake_state = FakeState().install(self)
        self.fake_state.seed_run("run-1", experiment_name="exp")
        self.fake_s3 = FakeS3()
        patcher = patch("cortexgrid.checkpoint.s3_util", self.fake_s3)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        clear_instance()
        set_cortexgrid_job_id("")

    def test_prefix_is_global_when_no_job_id(self) -> None:
        self.assertEqual(_checkpoint_prefix(), "checkpoint/global")

    def test_prefix_includes_job_id_when_set(self) -> None:
        set_cortexgrid_job_id("job-99")
        self.assertEqual(_checkpoint_prefix(), "checkpoint/job-99")

    def test_persist_uploads_manifest_and_attributes(self) -> None:
        with checkpoint() as ckpt:
            ckpt.epoch = 5
            ckpt.lr = 0.001

        prefix = _checkpoint_prefix()
        self.assertIn(("run-1", prefix), self.fake_state.checkpoints)
        manifest = self.fake_state.checkpoints[("run-1", prefix)]
        self.assertIn("epoch", manifest["attrs"])
        self.assertIn("lr", manifest["attrs"])

        uri = manifest["attrs"]["epoch"]["uri"]
        self.assertTrue(uri.startswith("s3://"))
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        self.assertTrue((self.fake_s3.root / bucket / key).exists())

    def test_load_restores_attributes(self) -> None:
        with checkpoint() as ckpt:
            ckpt.epoch = 5
            ckpt.lr = 0.001

        loaded = resume()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.epoch if loaded else -1, 5)
        self.assertEqual(loaded.lr if loaded else -1.0, 0.001)

    def test_load_returns_none_when_no_checkpoint(self) -> None:
        loaded = resume()
        self.assertIsNone(loaded)

    def test_checkpoint_not_persisted_on_exception(self) -> None:
        try:
            with checkpoint() as ckpt:
                ckpt.epoch = 5
                raise ValueError("boom")
        except ValueError:
            pass

        loaded = resume()
        self.assertIsNone(loaded)

    def test_setattr_and_getattr(self) -> None:
        ckpt = Checkpoint("test")
        ckpt.x = 42
        ckpt.y = "hello"
        self.assertEqual(ckpt.x, 42)
        self.assertEqual(ckpt.y, "hello")

    def test_missing_attr_raises(self) -> None:
        ckpt = Checkpoint("test")
        with self.assertRaises(AttributeError):
            _ = ckpt.nonexistent

    def test_bool_is_false_when_empty(self) -> None:
        self.assertFalse(Checkpoint("test"))

    def test_bool_is_true_when_populated(self) -> None:
        ckpt = Checkpoint("test")
        ckpt.x = 1
        self.assertTrue(ckpt)

    @unittest.skipUnless(HAS_TORCH, "torch not installed")
    def test_save_and_restore_torch_tensor(self) -> None:
        tensor = torch.tensor([1.0, 2.0, 3.0])
        with checkpoint() as ckpt:
            ckpt.weights = tensor

        loaded = resume()
        assert loaded is not None
        self.assertTrue(torch.equal(loaded.weights, tensor))

    @unittest.skipUnless(HAS_TORCH, "torch not installed")
    def test_save_and_restore_torch_state_dict(self) -> None:
        state = {"w": torch.tensor([[1.0, 2.0]]), "b": torch.tensor([0.5])}
        with checkpoint() as ckpt:
            ckpt.state = state

        loaded = resume()
        assert loaded is not None
        self.assertTrue(torch.equal(loaded.state["w"], state["w"]))
        self.assertTrue(torch.equal(loaded.state["b"], state["b"]))

    def test_save_and_restore_training_state(self) -> None:
        model = MagicMock()
        optimizer = MagicMock()
        model.state_dict.return_value = {"w": 1.0}
        optimizer.state_dict.return_value = {"lr": 0.01}

        ckpt = Checkpoint("test")
        ckpt.save_training_state(model, optimizer)

        model2 = MagicMock()
        optimizer2 = MagicMock()
        ckpt.restore_training_state(model2, optimizer2)

        model2.load_state_dict.assert_called_once_with({"w": 1.0})
        optimizer2.load_state_dict.assert_called_once_with({"lr": 0.01})


if __name__ == "__main__":
    unittest.main()
