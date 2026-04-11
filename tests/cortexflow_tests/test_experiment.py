from __future__ import annotations

import unittest
from unittest.mock import patch

from cortexflow.config import CortexConfig, get_config, set_config
from cortexflow.experiment import init


class TestInit(unittest.TestCase):
    def setUp(self) -> None:
        set_config(None)
        self.boto3_patcher = patch("boto3.client")
        self.mlflow_patcher = patch("cortexflow.mlflow_util.MlflowClient")
        self.boto3_patcher.start()
        self.mlflow_patcher.start()

    def tearDown(self) -> None:
        self.boto3_patcher.stop()
        self.mlflow_patcher.stop()
        set_config(None)

    def test_get_config_returns_none_before_init(self) -> None:
        self.assertIsNone(get_config())

    def test_init_creates_experiment_and_run(self) -> None:
        init()

        cfg = get_config()
        self.assertIsNotNone(cfg)
        self.assertTrue(cfg.experiment_name)
        self.assertTrue(cfg.run_id)

    def test_init_skips_when_already_initialized(self) -> None:
        existing = CortexConfig(
            experiment_name="my-exp",
            run_id="run-123",
        )
        set_config(existing)

        init()

        self.assertIs(get_config(), existing)

    def test_from_experiment_overrides_experiment_and_run_id(self) -> None:
        cfg = CortexConfig.from_experiment("my-exp", "run-xyz")

        self.assertEqual(cfg.experiment_name, "my-exp")
        self.assertEqual(cfg.run_id, "run-xyz")


if __name__ == "__main__":
    unittest.main()
