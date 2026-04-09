"""Configuration — pulls secrets from AWS Secrets Manager at runtime.

On Mac: cortexflow.init() fetches secrets from AWS SM (requires aws CLI configured).
On DGX (inside Ray job): reads env vars injected by cortexflow.remote.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


SM_PREFIX = "robolab/infra"
SM_REGION = "us-east-1"


def _get_secret(secret_id: str) -> str:
    """Fetch a secret from AWS Secrets Manager. Returns empty string on failure."""
    try:
        import boto3
        client = boto3.client("secretsmanager", region_name=SM_REGION)
        return client.get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception:
        return ""


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
    github_token: str = ""

    @staticmethod
    def from_env() -> CortexConfig:
        """Build config from env vars (used inside Ray jobs where env is pre-injected)."""
        dgx_ip = os.environ.get("DGX_TAILSCALE_IP", "")
        s3_endpoint = os.environ.get("MLFLOW_S3_ENDPOINT_URL", "")
        if not s3_endpoint and dgx_ip:
            s3_endpoint = f"http://{dgx_ip}:9000"

        return CortexConfig(
            ray_address=os.environ.get("RAY_ADDRESS", f"http://{dgx_ip}:8265" if dgx_ip else ""),
            dgx_ip=dgx_ip,
            mlflow_tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", f"http://{dgx_ip}:5000" if dgx_ip else ""),
            mlflow_s3_endpoint_url=s3_endpoint,
            s3_endpoint_url=s3_endpoint,
            s3_access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            s3_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            s3_default_bucket=os.environ.get("ARTIFACT_STORE_BUCKET", "ray-checkpoints"),
        )

    @staticmethod
    def from_secrets_manager() -> CortexConfig:
        """Build config by pulling secrets from AWS Secrets Manager."""
        dgx_ip = _get_secret(f"{SM_PREFIX}/DGX_TAILSCALE_IP")
        minio_password = _get_secret(f"{SM_PREFIX}/MINIO_ROOT_PASSWORD")
        github_token = _get_secret(f"{SM_PREFIX}/GH_TOKEN")
        s3_endpoint = f"http://{dgx_ip}:9000" if dgx_ip else ""

        return CortexConfig(
            ray_address=f"http://{dgx_ip}:8265" if dgx_ip else "",
            dgx_ip=dgx_ip,
            mlflow_tracking_uri=f"http://{dgx_ip}:5000" if dgx_ip else "",
            mlflow_s3_endpoint_url=s3_endpoint,
            s3_endpoint_url=s3_endpoint,
            s3_access_key="minioadmin",
            s3_secret_key=minio_password,
            s3_default_bucket="ray-checkpoints",
            github_token=github_token,
        )

    def env_vars_for_job(self) -> dict[str, str]:
        """Return env vars to inject into Ray jobs so task code can use from_env()."""
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
