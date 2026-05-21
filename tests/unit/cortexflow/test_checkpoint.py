from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from cortexflow.checkpoint import (
    Checkpoint,
    _checkpoint_prefix,
    checkpoint,
    resume,
    set_cortexflow_job_id,
)
from cortexflow.experiment import Experiment, clear_instance, set_instance


def _make_experiment() -> Experiment:
    return Experiment(
        experiment_name="exp",
        run_id="run-1",
    )


class FakeMLflow:
    """Fake MlflowClient backed by a temp directory."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def log_artifact(
        self, run_id: str, local_path: str, artifact_path: str = ""
    ) -> None:
        dest = self.root / artifact_path
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest / Path(local_path).name)

    def download_artifacts(self, run_id: str, path: str) -> str:
        local = self.root / path
        if not local.exists():
            raise FileNotFoundError(path)
        return str(local)

    def list_artifacts(self, run_id: str, path: str = "") -> list:
        parent = self.root / path if path else self.root
        if not parent.is_dir():
            return []
        return [
            SimpleNamespace(path=f"{path}/{p.name}" if path else p.name)
            for p in parent.iterdir()
        ]


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
        self.fake_mlflow = FakeMLflow()
        self.fake_s3 = FakeS3()
        patchers = [
            patch("cortexflow.checkpoint.MlflowClient", return_value=self.fake_mlflow),
            patch(
                "cortexflow.checkpoint.get_mlflow_tracking_uri",
                return_value="http://test:5000",
            ),
            patch("cortexflow.checkpoint.s3_util", self.fake_s3),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        clear_instance()
        set_cortexflow_job_id("")

    def test_prefix_is_global_when_no_job_id(self) -> None:
        self.assertEqual(_checkpoint_prefix(), "checkpoint/global")

    def test_prefix_includes_job_id_when_set(self) -> None:
        set_cortexflow_job_id("job-99")
        self.assertEqual(_checkpoint_prefix(), "checkpoint/job-99")

    def test_persist_uploads_manifest_and_attributes(self) -> None:
        with checkpoint() as ckpt:
            ckpt.epoch = 5
            ckpt.lr = 0.001

        prefix = _checkpoint_prefix()
        manifest_path = self.fake_mlflow.root / prefix / "manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text())
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
