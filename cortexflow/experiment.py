from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from cortexflow.secrets import get_secret
from haikunator import Haikunator  # type: ignore
from mlflow.tracking import MlflowClient


SM_PREFIX = "robolab/infra"
_SINGLETON_EXPERIMENT: "Experiment | None" = None
_RUNS_ON_DGX = False


def set_runs_on_dgx(flag: bool) -> None:
    global _RUNS_ON_DGX
    _RUNS_ON_DGX = flag


def runs_on_dgx() -> bool:
    global _RUNS_ON_DGX
    return _RUNS_ON_DGX


def get_mlflow_tracking_uri() -> str:
    """Build the MLflow tracking URI from secrets."""
    if runs_on_dgx():
        return "http://mlflow:5000"
    dgx_ip = get_secret(f"{SM_PREFIX}/DGX_TAILSCALE_IP")
    return f"http://{dgx_ip}:5000" if dgx_ip else ""


def get_ray_address() -> str:
    """Build the Ray address from secrets."""
    if runs_on_dgx():
        return "http://ray-head:8265"
    dgx_ip = get_secret(f"{SM_PREFIX}/DGX_TAILSCALE_IP")
    return f"http://{dgx_ip}:8265" if dgx_ip else ""


def get_s3_endpoint_url() -> str:
    """Build the S3/MinIO endpoint URL from secrets."""
    if runs_on_dgx():
        return "http://minio:9000"
    dgx_ip = get_secret(f"{SM_PREFIX}/DGX_TAILSCALE_IP")
    return f"http://{dgx_ip}:9000" if dgx_ip else ""


@dataclass
class Experiment:
    experiment_name: str
    run_id: str
    s3_access_key: str
    s3_secret_key: str
    s3_default_bucket: str
    github_token: str

    @classmethod
    def init(cls, name: str | None = None) -> "Experiment":
        """Create a new MLflow experiment+run. Once per process."""
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
        )

        if _SINGLETON_EXPERIMENT is not None:
            if name is not None and _SINGLETON_EXPERIMENT.experiment_name != name:
                raise ValueError(
                    f"Active experiment has a different name {name} != "
                    f"{_SINGLETON_EXPERIMENT.experiment_name}"
                )
            return _SINGLETON_EXPERIMENT

        secrets = _pull_secrets()
        experiment_name, run_id = _try_create_experiment_and_run(
            experiment=name,
            mlflow_tracking_uri=get_mlflow_tracking_uri(),
        )
        instance = cls(
            experiment_name=experiment_name,
            run_id=run_id,
            **secrets,
        )
        set_instance(instance)
        return instance

    @classmethod
    def from_experiment(cls, experiment_name: str, run_id: str) -> "Experiment":
        """Bind to an existing MLflow experiment+run. Once per process."""
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
        )

        if _SINGLETON_EXPERIMENT is not None:
            if (
                _SINGLETON_EXPERIMENT.experiment_name != experiment_name
                or _SINGLETON_EXPERIMENT.run_id != run_id
            ):
                raise ValueError(
                    f"Active experiment is different to the requested one: "
                    f"({experiment_name}, {run_id}) != "
                    f"({_SINGLETON_EXPERIMENT.experiment_name}, {_SINGLETON_EXPERIMENT.run_id})"
                )
            return _SINGLETON_EXPERIMENT

        secrets = _pull_secrets()
        instance = cls(
            experiment_name=experiment_name,
            run_id=run_id,
            **secrets,
        )
        set_instance(instance)
        return instance

    def get_jobs(self) -> list[str]:
        """Return cortexflow job IDs submitted against this experiment+run."""
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        return [
            Path(f.path).name
            for f in client.list_artifacts(self.run_id, path="job")
            if f.is_dir
        ]

    @classmethod
    def get_instance(cls) -> "Experiment":
        if _SINGLETON_EXPERIMENT is None:
            raise ValueError(
                "Call Experiment.init or Experiment.from_experiment first"
            )
        return _SINGLETON_EXPERIMENT


def set_instance(instance: Experiment | None) -> None:
    global _SINGLETON_EXPERIMENT
    _SINGLETON_EXPERIMENT = instance


def clear_instance() -> None:
    # Use only in tests to clean between tests
    global _SINGLETON_EXPERIMENT
    _SINGLETON_EXPERIMENT = None


def _try_create_experiment_and_run(
    experiment: str | None, mlflow_tracking_uri: str
) -> tuple[str, str]:
    name_gen = Haikunator()
    if experiment is None:
        experiment = name_gen.haikunate(token_length=2, token_chars="0123456789")

    client = MlflowClient(tracking_uri=mlflow_tracking_uri)
    experiment_obj = client.get_experiment_by_name(name=experiment)
    if experiment_obj:
        experiment_id = experiment_obj.experiment_id
    else:
        experiment_id = client.create_experiment(name=experiment)

    run_name = name_gen.haikunate(token_length=2, token_chars="0123456789")
    run = client.create_run(experiment_id=experiment_id, run_name=run_name)

    return (experiment, run.info.run_id)


def _pull_secrets() -> dict[str, str]:
    """Pull connection info from AWS Secrets Manager."""
    s3_secret_key = get_secret(f"{SM_PREFIX}/MINIO_ROOT_PASSWORD")
    github_token = get_secret(f"{SM_PREFIX}/GH_TOKEN")

    return dict(
        s3_access_key="minioadmin",
        s3_secret_key=s3_secret_key,
        s3_default_bucket="ray-checkpoints",
        github_token=github_token,
    )
    