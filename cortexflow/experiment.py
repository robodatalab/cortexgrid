from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from cortexflow import s3_util
from cortexflow.infra import get_mlflow_tracking_uri
from cortexflow.jobs import stop_experiment_run_jobs
from cortexflow.ray_util import list_ray_jobs_with_submission_id, stop_ray_job
from haikunator import Haikunator  # type: ignore
from mlflow.tracking import MlflowClient


_SINGLETON_EXPERIMENT: "Experiment | None" = None


@dataclass
class Experiment:
    experiment_name: str
    run_id: str

    def run_name(self) -> str:
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        return client.get_run(self.run_id).info.run_name or self.run_id

    @classmethod
    def init(cls, name: str | None = None) -> "Experiment":
        """Create a new MLflow experiment+run. Once per process."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        if _SINGLETON_EXPERIMENT is not None:
            if name is not None and _SINGLETON_EXPERIMENT.experiment_name != name:
                raise ValueError(
                    f"Active experiment has a different name {name} != "
                    f"{_SINGLETON_EXPERIMENT.experiment_name}"
                )
            return _SINGLETON_EXPERIMENT

        experiment_name, run_id = _try_create_experiment_and_run(
            experiment=name,
            mlflow_tracking_uri=get_mlflow_tracking_uri(),
        )
        instance = cls(
            experiment_name=experiment_name,
            run_id=run_id,
        )
        set_instance(instance)
        return instance

    @classmethod
    def from_experiment(cls, experiment_name: str, run_id: str) -> "Experiment":
        """Bind to an existing MLflow experiment+run. Once per process."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
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

        instance = cls(
            experiment_name=experiment_name,
            run_id=run_id,
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
            raise ValueError("Call Experiment.init or Experiment.from_experiment first")
        return _SINGLETON_EXPERIMENT

    @classmethod
    def close(cls) -> None:
        """Detach the active experiment so a different one can be init'd in this process."""
        set_instance(None)


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


def delete_run(run_id: str) -> None:
    """Soft-delete a run in MLflow, cancel its Ray attempts, and wipe its
    S3 job packages so it cannot be relaunched or re-read.

    stop_experiment_run_jobs runs first so the control plane stops spawning
    fresh Ray attempts for retry=True jobs before we tear the run down."""
    stop_experiment_run_jobs(run_id)
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    job_ids = [
        Path(f.path).name
        for f in client.list_artifacts(run_id, path="job")
        if f.is_dir
    ]
    all_submissions = list_ray_jobs_with_submission_id()
    for job_id in job_ids:
        prefix = f"{run_id}-{job_id}-"
        for sid in all_submissions:
            if sid.startswith(prefix):
                stop_ray_job(sid)
        s3_util.delete_prefix(f"job/{job_id}/")
    client.delete_run(run_id)


def list_run_ids_in_experiment(name: str) -> list[str]:
    """Return the run IDs of every active run in the named experiment."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    exp = client.get_experiment_by_name(name)
    if exp is None:
        return []
    return [
        r.info.run_id
        for r in client.search_runs(experiment_ids=[exp.experiment_id])
    ]


def delete_experiment(name: str) -> None:
    """Soft-delete every run in the experiment, then the experiment itself.

    Idempotent: already-deleted experiments are treated as success."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    exp = client.get_experiment_by_name(name)
    if exp is None or exp.lifecycle_stage != "active":
        return
    for run in client.search_runs(experiment_ids=[exp.experiment_id]):
        delete_run(run.info.run_id)
    client.delete_experiment(exp.experiment_id)


def list_experiments() -> list[Experiment]:
    """Map MLflow experiment names to their run IDs."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    result: list[Experiment] = []
    for exp in client.search_experiments():
        runs = client.search_runs(experiment_ids=[exp.experiment_id])
        for run in runs:
            result.append(Experiment(exp.name, run_id=run.info.run_id))
    return result
