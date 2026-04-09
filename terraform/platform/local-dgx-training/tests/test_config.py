from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from cortexflow.config import CortexConfig, get_config, set_config


class TestCortexConfigFromEnv(unittest.TestCase):
    """Tests for from_env() — used inside Ray jobs where env vars are pre-injected."""

    def setUp(self) -> None:
        self.env_patcher = patch.dict(os.environ, {}, clear=True)
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_empty_env_returns_empty_config(self) -> None:
        config = CortexConfig.from_env()
        self.assertEqual(config.ray_address, "")
        self.assertEqual(config.dgx_ip, "")
        self.assertEqual(config.mlflow_tracking_uri, "")
        self.assertEqual(config.s3_endpoint_url, "")
        self.assertEqual(config.s3_default_bucket, "ray-checkpoints")

    def test_dgx_ip_derives_all_urls(self) -> None:
        os.environ["DGX_TAILSCALE_IP"] = "100.1.2.3"
        config = CortexConfig.from_env()
        self.assertEqual(config.dgx_ip, "100.1.2.3")
        self.assertEqual(config.ray_address, "http://100.1.2.3:8265")
        self.assertEqual(config.mlflow_tracking_uri, "http://100.1.2.3:5000")
        self.assertEqual(config.s3_endpoint_url, "http://100.1.2.3:9000")

    def test_explicit_env_vars_override_derived(self) -> None:
        os.environ["DGX_TAILSCALE_IP"] = "100.1.2.3"
        os.environ["RAY_ADDRESS"] = "http://custom:9999"
        os.environ["MLFLOW_TRACKING_URI"] = "http://custom:5555"
        os.environ["MLFLOW_S3_ENDPOINT_URL"] = "http://custom:8888"
        config = CortexConfig.from_env()
        self.assertEqual(config.ray_address, "http://custom:9999")
        self.assertEqual(config.mlflow_tracking_uri, "http://custom:5555")
        self.assertEqual(config.s3_endpoint_url, "http://custom:8888")

    def test_s3_credentials_from_env(self) -> None:
        os.environ["AWS_ACCESS_KEY_ID"] = "AKIA_TEST"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "secret123"
        config = CortexConfig.from_env()
        self.assertEqual(config.s3_access_key, "AKIA_TEST")
        self.assertEqual(config.s3_secret_key, "secret123")

    def test_custom_default_bucket(self) -> None:
        os.environ["ARTIFACT_STORE_BUCKET"] = "my-bucket"
        config = CortexConfig.from_env()
        self.assertEqual(config.s3_default_bucket, "my-bucket")


class TestCortexConfigFromSecretsManager(unittest.TestCase):
    """Tests for from_secrets_manager() — used on the Mac."""

    @patch("cortexflow.config._get_secret")
    def test_builds_config_from_secrets(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = lambda sid: {
            "robolab/infra/DGX_TAILSCALE_IP": "100.1.2.3",
            "robolab/infra/MINIO_ROOT_PASSWORD": "minio-secret",
            "robolab/infra/GH_TOKEN": "ghp_test123",
        }[sid]

        config = CortexConfig.from_secrets_manager()
        self.assertEqual(config.dgx_ip, "100.1.2.3")
        self.assertEqual(config.ray_address, "http://100.1.2.3:8265")
        self.assertEqual(config.mlflow_tracking_uri, "http://100.1.2.3:5000")
        self.assertEqual(config.s3_endpoint_url, "http://100.1.2.3:9000")
        self.assertEqual(config.s3_access_key, "minioadmin")
        self.assertEqual(config.s3_secret_key, "minio-secret")

    @patch("cortexflow.config._get_secret")
    def test_empty_secrets_returns_empty_config(self, mock_get: MagicMock) -> None:
        mock_get.return_value = ""
        config = CortexConfig.from_secrets_manager()
        self.assertEqual(config.dgx_ip, "")
        self.assertEqual(config.ray_address, "")
        self.assertEqual(config.s3_endpoint_url, "")


class TestEnvVarsForJob(unittest.TestCase):
    def test_full_config_exports_all_vars(self) -> None:
        config = CortexConfig(
            dgx_ip="100.1.2.3",
            mlflow_tracking_uri="http://100.1.2.3:5000",
            mlflow_s3_endpoint_url="http://100.1.2.3:9000",
            s3_access_key="key",
            s3_secret_key="secret",
        )
        env = config.env_vars_for_job()
        self.assertEqual(env["MLFLOW_TRACKING_URI"], "http://100.1.2.3:5000")
        self.assertEqual(env["MLFLOW_S3_ENDPOINT_URL"], "http://100.1.2.3:9000")
        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "key")
        self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "secret")
        self.assertEqual(env["DGX_TAILSCALE_IP"], "100.1.2.3")

    def test_empty_config_exports_nothing(self) -> None:
        config = CortexConfig()
        env = config.env_vars_for_job()
        self.assertEqual(env, {})

    def test_partial_config_exports_only_set_fields(self) -> None:
        config = CortexConfig(mlflow_tracking_uri="http://x:5000")
        env = config.env_vars_for_job()
        self.assertEqual(env, {"MLFLOW_TRACKING_URI": "http://x:5000"})


class TestConfigSingleton(unittest.TestCase):
    def setUp(self) -> None:
        set_config(None)  # type: ignore[arg-type]

    def test_get_config_raises_before_init(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            get_config()
        self.assertIn("cortexflow.init()", str(ctx.exception))

    def test_set_then_get(self) -> None:
        config = CortexConfig(dgx_ip="1.2.3.4")
        set_config(config)
        self.assertIs(get_config(), config)


if __name__ == "__main__":
    unittest.main()
