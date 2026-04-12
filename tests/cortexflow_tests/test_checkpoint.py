from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import cloudpickle  # type: ignore

from cortexflow.checkpoint import (
    Checkpoint,
    _checkpoint_prefix,
    _deserialize,
    _serialize,
    checkpoint,
    get_cortexflow_job_id,
    resume,
    set_cortexflow_job_id,
)
from cortexflow.experiment import Experiment, clear_instance, set_instance


def _make_experiment() -> Experiment:
    return Experiment(
        experiment_name="exp",
        run_id="run-1",
        s3_access_key="",
        s3_secret_key="",
        s3_default_bucket="",
        github_token="",
    )


class FakeMLflow:
    """Fake MlflowClient backed by a temp directory."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def log_artifact(self, run_id: str, local_path: str, artifact_path: str = "") -> None:
        dest = self.root / artifact_path
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest / Path(local_path).name)

    def download_artifacts(self, run_id: str, path: str) -> str:
        local = self.root / path
        if not local.exists():
            raise FileNotFoundError(path)
        return str(local)


def _reset_job_id() -> None:
    import sys
    sys.modules["cortexflow.checkpoint"]._CORTEXFLOW_JOB_ID = None


class TestJobId(unittest.TestCase):
    def setUp(self) -> None:
        _reset_job_id()

    def tearDown(self) -> None:
        _reset_job_id()

    def test_get_returns_none_initially(self) -> None:
        self.assertIsNone(get_cortexflow_job_id())

    def test_set_then_get(self) -> None:
        set_cortexflow_job_id("job-42")
        self.assertEqual(get_cortexflow_job_id(), "job-42")


class TestCheckpointPrefix(unittest.TestCase):
    def setUp(self) -> None:
        _reset_job_id()
        clear_instance()
        set_instance(_make_experiment())

    def tearDown(self) -> None:
        _reset_job_id()
        clear_instance()

    def test_prefix_is_global_when_no_job_id(self) -> None:
        self.assertEqual(_checkpoint_prefix(), "checkpoint/global")

    def test_prefix_includes_job_id_when_set(self) -> None:
        set_cortexflow_job_id("job-99")
        self.assertEqual(_checkpoint_prefix(), "checkpoint/job-99")


class TestCheckpointPersistAndLoad(unittest.TestCase):
    def setUp(self) -> None:
        clear_instance()
        set_instance(_make_experiment())
        self.fake_mlflow = FakeMLflow()
        self.patcher = patch(
            "cortexflow.checkpoint.MlflowClient",
            return_value=self.fake_mlflow,
        )
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        clear_instance()

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

        epoch_file = self.fake_mlflow.root / prefix / manifest["attrs"]["epoch"]["file"]
        self.assertTrue(epoch_file.exists())

    def test_load_restores_attributes(self) -> None:
        with checkpoint() as ckpt:
            ckpt.epoch = 5
            ckpt.lr = 0.001

        loaded = resume()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.epoch, 5)
        self.assertEqual(loaded.lr, 0.001)

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


class TestCheckpointAttributes(unittest.TestCase):
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
