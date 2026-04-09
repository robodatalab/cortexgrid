"""Configuration and context detection.

Reads env vars to determine where services live. Works in all contexts:
- Mac (dev): env vars set by setup-mac.sh
- DGX (inside Ray job): env vars injected by cortexflow.remote
- AWS EC2 (future): env vars set on the instance
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class CortexConfig:
    ray_address: str = ""
    dgx_ip: str = ""

    mlflow_tracking_uri: str = ""
    mlflow_s3_endpoint_url: str = ""

    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_default_bucket: str = "ray-checkpoints"

    @staticmethod
    def from_env() -> CortexConfig:
        dgx_ip = os.environ.get("DGX_TAILSCALE_IP", "")

        ray_address = os.environ.get("RAY_ADDRESS", "")
        if not ray_address and dgx_ip:
            ray_address = f"http://{dgx_ip}:8265"

        mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "")
        if not mlflow_uri and dgx_ip:
            mlflow_uri = f"http://{dgx_ip}:5000"

        s3_endpoint = os.environ.get(
            "MLFLOW_S3_ENDPOINT_URL",
            os.environ.get("ARTIFACT_STORE_ENDPOINT", ""),
        )
        if not s3_endpoint and dgx_ip:
            s3_endpoint = f"http://{dgx_ip}:9000"

        return CortexConfig(
            ray_address=ray_address,
            dgx_ip=dgx_ip,
            mlflow_tracking_uri=mlflow_uri,
            mlflow_s3_endpoint_url=s3_endpoint,
            s3_endpoint_url=s3_endpoint,
            s3_access_key=os.environ.get(
                "AWS_ACCESS_KEY_ID",
                os.environ.get("ARTIFACT_STORE_ACCESS_KEY", ""),
            ),
            s3_secret_key=os.environ.get(
                "AWS_SECRET_ACCESS_KEY",
                os.environ.get("ARTIFACT_STORE_SECRET_KEY", ""),
            ),
            s3_default_bucket=os.environ.get("ARTIFACT_STORE_BUCKET", "ray-checkpoints"),
        )

    def env_vars_for_job(self) -> dict[str, str]:
        """Return env vars to inject into Ray jobs so task code can call cortexflow.init()."""
        env: dict[str, str] = {}
        if self.mlflow_tracking_uri:
            env["MLFLOW_TRACKING_URI"] = self.mlflow_tracking_uri
        if self.mlflow_s3_endpoint_url:
            env["MLFLOW_S3_ENDPOINT_URL"] = self.mlflow_s3_endpoint_url
        if self.s3_access_key:
            env["AWS_ACCESS_KEY_ID"] = self.s3_access_key
        if self.s3_secret_key:
            env["AWS_SECRET_ACCESS_KEY"] = self.s3_secret_key
        if self.dgx_ip:
            env["DGX_TAILSCALE_IP"] = self.dgx_ip
        return env


_config: CortexConfig | None = None


def get_config() -> CortexConfig:
    if _config is None:
        raise RuntimeError("Call cortexflow.init() first")
    return _config


def set_config(config: CortexConfig) -> None:
    global _config
    _config = config
