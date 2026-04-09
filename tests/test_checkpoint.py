from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

import cloudpickle  # type: ignore

from cortexflow.config import CortexConfig, set_config


class TestCheckpointAttributes(unittest.TestCase):
    def test_setattr_stores_in_data(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1")
        ckpt.epoch = 5
        ckpt.lr = 0.001
        self.assertEqual(ckpt._data, {"epoch": 5, "lr": 0.001})

    def test_getattr_reads_from_data(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1", _data={"epoch": 3, "loss": 0.5})
        self.assertEqual(ckpt.epoch, 3)
        self.assertEqual(ckpt.loss, 0.5)

    def test_getattr_raises_for_missing(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1")
        with self.assertRaises(AttributeError):
            _ = ckpt.nonexistent

    def test_bool_false_when_empty(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1")
        self.assertFalse(ckpt)

    def test_bool_true_when_has_data(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1", _data={"epoch": 0})
        self.assertTrue(ckpt)

    def test_repr(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1", _data={"epoch": 5, "lr": 0.001})
        r = repr(ckpt)
        self.assertIn("job-1", r)
        self.assertIn("epoch", r)


class TestCheckpointContextManager(unittest.TestCase):
    def test_enter_returns_self(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1")
        self.assertIs(ckpt.__enter__(), ckpt)

    @patch("cortexflow.checkpoint.Checkpoint._persist")
    def test_exit_calls_persist_on_clean_exit(self, mock_persist: MagicMock) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1")
        with ckpt:
            ckpt.epoch = 5
        mock_persist.assert_called_once()

    @patch("cortexflow.checkpoint.Checkpoint._persist")
    def test_exit_does_not_persist_on_exception(self, mock_persist: MagicMock) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-1")
        with self.assertRaises(ValueError):
            with ckpt:
                ckpt.epoch = 5
                raise ValueError("boom")
        mock_persist.assert_not_called()


class TestSaveTrainingState(unittest.TestCase):
    def test_saves_model_and_optimizer_state_dicts(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        model = MagicMock()
        model.state_dict.return_value = {"weight": "model_data"}
        optimizer = MagicMock()
        optimizer.state_dict.return_value = {"lr": 0.001}

        ckpt = Checkpoint("job-1")
        ckpt.save_training_state(model, optimizer)

        self.assertEqual(ckpt._data["_model_state"], {"weight": "model_data"})
        self.assertEqual(ckpt._data["_optimizer_state"], {"lr": 0.001})
        self.assertNotIn("_scheduler_state", ckpt._data)

    def test_saves_scheduler_when_provided(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        model = MagicMock()
        model.state_dict.return_value = {}
        optimizer = MagicMock()
        optimizer.state_dict.return_value = {}
        scheduler = MagicMock()
        scheduler.state_dict.return_value = {"last_epoch": 10}

        ckpt = Checkpoint("job-1")
        ckpt.save_training_state(model, optimizer, scheduler)

        self.assertEqual(ckpt._data["_scheduler_state"], {"last_epoch": 10})


class TestRestoreTrainingState(unittest.TestCase):
    def test_restores_model_and_optimizer(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint(
            "job-1",
            _data={
                "_model_state": {"weight": "data"},
                "_optimizer_state": {"lr": 0.001},
            },
        )
        model = MagicMock()
        optimizer = MagicMock()

        ckpt.restore_training_state(model, optimizer)

        model.load_state_dict.assert_called_once_with({"weight": "data"})
        optimizer.load_state_dict.assert_called_once_with({"lr": 0.001})

    def test_restores_scheduler_when_available(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint(
            "job-1",
            _data={
                "_model_state": {},
                "_optimizer_state": {},
                "_scheduler_state": {"last_epoch": 10},
            },
        )
        model = MagicMock()
        optimizer = MagicMock()
        scheduler = MagicMock()

        ckpt.restore_training_state(model, optimizer, scheduler)

        scheduler.load_state_dict.assert_called_once_with({"last_epoch": 10})

    def test_skips_scheduler_when_not_in_checkpoint(self) -> None:
        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint(
            "job-1",
            _data={
                "_model_state": {},
                "_optimizer_state": {},
            },
        )
        model = MagicMock()
        optimizer = MagicMock()
        scheduler = MagicMock()

        ckpt.restore_training_state(model, optimizer, scheduler)
        scheduler.load_state_dict.assert_not_called()


class TestSerialization(unittest.TestCase):
    def test_cloudpickle_for_primitives(self) -> None:
        from cortexflow.checkpoint import _serialize, _deserialize

        data, fmt = _serialize(42)
        self.assertEqual(fmt, "cloudpickle")
        self.assertEqual(_deserialize(data, "cloudpickle"), 42)

    def test_cloudpickle_for_dicts(self) -> None:
        from cortexflow.checkpoint import _serialize, _deserialize

        value = {"lr": 0.001, "name": "test"}
        data, fmt = _serialize(value)
        self.assertEqual(fmt, "cloudpickle")
        self.assertEqual(_deserialize(data, "cloudpickle"), value)

    def test_torch_for_tensor_dicts(self) -> None:
        import torch
        from cortexflow.checkpoint import (
            _serialize,
            _deserialize,
            _is_torch_serializable,
        )

        state_dict = {"weight": torch.tensor([1.0, 2.0, 3.0])}
        self.assertTrue(_is_torch_serializable(state_dict))

        data, fmt = _serialize(state_dict)
        self.assertEqual(fmt, "torch")

        restored = _deserialize(data, "torch")
        self.assertTrue(torch.equal(restored["weight"], state_dict["weight"]))

    def test_ext_for_format(self) -> None:
        from cortexflow.checkpoint import _ext_for

        self.assertEqual(_ext_for("torch"), ".pt")
        self.assertEqual(_ext_for("cloudpickle"), ".pkl")


class TestPersist(unittest.TestCase):
    def setUp(self) -> None:
        set_config(
            CortexConfig(
                s3_endpoint_url="http://localhost:9000",
                s3_access_key="k",
                s3_secret_key="s",
            )
        )

    def tearDown(self) -> None:
        set_config(None)  # type: ignore[arg-type]

    @patch("cortexflow.checkpoint.get_s3_client")
    def test_uploads_attributes_and_manifest(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint("job-abc")
        ckpt.epoch = 5
        ckpt.loss = 0.3
        ckpt._persist()

        put_calls = mock_client.put_object.call_args_list
        keys_uploaded = [c[1]["Key"] for c in put_calls]

        self.assertIn("job-abc/epoch.pkl", keys_uploaded)
        self.assertIn("job-abc/loss.pkl", keys_uploaded)
        self.assertIn("job-abc/manifest.json", keys_uploaded)

        manifest_call = [
            c for c in put_calls if c[1]["Key"] == "job-abc/manifest.json"
        ][0]
        manifest = json.loads(manifest_call[1]["Body"])
        self.assertIn("epoch", manifest["attrs"])
        self.assertEqual(manifest["attrs"]["epoch"]["format"], "cloudpickle")


class TestLoad(unittest.TestCase):
    def setUp(self) -> None:
        set_config(
            CortexConfig(
                s3_endpoint_url="http://localhost:9000",
                s3_access_key="k",
                s3_secret_key="s",
            )
        )

    def tearDown(self) -> None:
        set_config(None)  # type: ignore[arg-type]

    @patch("cortexflow.checkpoint.get_s3_client")
    def test_loads_checkpoint_from_s3(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        manifest = {
            "attrs": {
                "epoch": {"file": "epoch.pkl", "format": "cloudpickle"},
                "loss": {"file": "loss.pkl", "format": "cloudpickle"},
            }
        }

        def get_object(Bucket, Key):
            body = MagicMock()
            if Key.endswith("manifest.json"):
                body.read.return_value = json.dumps(manifest).encode()
            elif Key.endswith("epoch.pkl"):
                body.read.return_value = cloudpickle.dumps(5)
            elif Key.endswith("loss.pkl"):
                body.read.return_value = cloudpickle.dumps(0.3)
            return {"Body": body}

        mock_client.get_object = get_object

        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint._load("job-abc")

        assert ckpt is not None
        self.assertEqual(ckpt.epoch, 5)
        self.assertAlmostEqual(ckpt.loss, 0.3)

    @patch("cortexflow.checkpoint.get_s3_client")
    def test_returns_none_when_no_checkpoint(self, mock_client_fn: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        no_such_key = type("NoSuchKey", (Exception,), {})
        mock_client.exceptions.NoSuchKey = no_such_key
        mock_client.get_object.side_effect = no_such_key()

        from cortexflow.checkpoint import Checkpoint

        ckpt = Checkpoint._load("job-nonexistent")
        self.assertIsNone(ckpt)


class TestGetJobId(unittest.TestCase):
    def test_returns_env_var(self) -> None:
        with patch.dict(os.environ, {"CORTEXFLOW_JOB_ID": "job-xyz"}):
            from cortexflow.checkpoint import get_job_id

            self.assertEqual(get_job_id(), "job-xyz")

    def test_raises_when_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            from cortexflow.checkpoint import get_job_id

            with self.assertRaises(RuntimeError):
                get_job_id()


class TestPublicFunctions(unittest.TestCase):
    def test_checkpoint_returns_checkpoint_with_job_id(self) -> None:
        with patch.dict(os.environ, {"CORTEXFLOW_JOB_ID": "job-123"}):
            from cortexflow.checkpoint import checkpoint, Checkpoint

            ckpt = checkpoint()
            assert isinstance(ckpt, Checkpoint)
            self.assertEqual(ckpt._job_id, "job-123")

    def test_checkpoint_returns_noop_without_job_id(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            from cortexflow.checkpoint import checkpoint, _NoOpCheckpoint

            ckpt = checkpoint()
            self.assertIsInstance(ckpt, _NoOpCheckpoint)

    def test_resume_returns_none_without_job_id(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            from cortexflow.checkpoint import resume

            self.assertIsNone(resume())

    @patch("cortexflow.checkpoint.Checkpoint._load", return_value=None)
    def test_resume_returns_none_when_no_checkpoint(self, _load: MagicMock) -> None:
        with patch.dict(os.environ, {"CORTEXFLOW_JOB_ID": "job-123"}):
            from cortexflow.checkpoint import resume

            self.assertIsNone(resume())

    def test_resume_delegates_to_load(self) -> None:
        mock_ckpt = MagicMock()
        with patch.dict(os.environ, {"CORTEXFLOW_JOB_ID": "job-123"}):
            with patch(
                "cortexflow.checkpoint.Checkpoint._load", return_value=mock_ckpt
            ) as mock_load:
                from cortexflow.checkpoint import resume

                result = resume()
                mock_load.assert_called_once_with("job-123")
                self.assertIs(result, mock_ckpt)


if __name__ == "__main__":
    unittest.main()
