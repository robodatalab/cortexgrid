"""cortexgrid's experiments and runs.

cortexgrid keeps its own record of each experiment and run with the jobs
control plane. Each maps onto an MLflow experiment and run, which exist only
so MLflow can display the run's metrics and params; the run id is MLflow's."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from cortexgrid import s3_util, state
from cortexgrid.infra import get_mlflow_tracking_uri
from cortexgrid.jobs import list_experiment_run_jobs, stop_experiment_run_jobs
from cortexgrid.ray_util import list_ray_jobs_with_submission_id, stop_ray_job
from cortexgrid.model_storage import delete_models_for_run
from haikunator import Haikunator  # type: ignore
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient


log = logging.getLogger(__name__)
_SINGLETON_EXPERIMENT: Experiment | None = None


@dataclass
class Experiment:
    experiment_name: str
    run_id: str

    def run_name(self) -> str:
        return state.get("runs", self.run_id)["run_name"]

    @classmethod
    def init(cls, name: str | None = None) -> "Experiment":
        """Create a new run, in a new or existing experiment. Once per process."""
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
        """Bind to an existing experiment+run. Once per process."""
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
        """Return cortexgrid job IDs submitted against this experiment+run."""
        return [job.job_id for job in list_experiment_run_jobs(self.run_id)]

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
    record = state.get("experiments", experiment)
    if record is None:
        # A concurrent init of the same new experiment may win the insert; the
        # record returned is the winner's, and its MLflow experiment is used.
        record = state.put(
            "experiments",
            experiment,
            body={"mlflow_experiment_id": _create_mlflow_experiment(client, experiment)},
        )

    run_name = name_gen.haikunate(token_length=2, token_chars="0123456789")
    run = client.create_run(
        experiment_id=record["mlflow_experiment_id"], run_name=run_name
    )
    state.put(
        "runs",
        run.info.run_id,
        body={"run_name": run_name, "experiment_name": experiment},
    )
    return (experiment, run.info.run_id)


def _create_mlflow_experiment(client: MlflowClient, name: str) -> str:
    """Create the MLflow experiment an experiment's runs log their metrics to.

    MLflow reserves a name forever, deleted experiments included, so a name it
    still holds from an earlier experiment gets a unique suffix. Nothing looks
    an MLflow experiment up by name: cortexgrid's record keeps its id."""
    try:
        return client.create_experiment(name=name)
    except MlflowException as exc:
        if exc.error_code != "RESOURCE_ALREADY_EXISTS":
            raise
        return client.create_experiment(name=f"{name}__{uuid.uuid4().hex[:8]}")


def delete_run(run_id: str) -> None:
    """Delete a run: cancel its Ray attempts, wipe its S3 job packages and
    models, soft-delete its MLflow run, and drop cortexgrid's record of it
    (its jobs, results and checkpoints go with it).

    stop_experiment_run_jobs runs first so the control plane stops spawning
    fresh Ray attempts for retry=True jobs before we tear the run down.

    Idempotent: a run cortexgrid has no record of is treated as success."""
    log.info("delete_run(%s): start", run_id)
    if state.get("runs", run_id) is None:
        log.info("delete_run(%s): early-exit, run not found", run_id)
        return
    stop_experiment_run_jobs(run_id)
    log.info("delete_run(%s): stop_experiment_run_jobs done", run_id)
    job_ids = [job.job_id for job in list_experiment_run_jobs(run_id)]
    log.info("delete_run(%s): %d job(s) to clean", run_id, len(job_ids))
    all_submissions = list_ray_jobs_with_submission_id()
    for job_id in job_ids:
        prefix = f"{run_id}-{job_id}-"
        for sid in all_submissions:
            if sid.startswith(prefix):
                stop_ray_job(sid)
        s3_util.delete_prefix(f"job/{job_id}/")
    delete_models_for_run(run_id)
    MlflowClient(tracking_uri=get_mlflow_tracking_uri()).delete_run(run_id)
    state.delete("runs", run_id)
    log.info("delete_run(%s): done", run_id)


def list_run_ids_in_experiment(name: str) -> list[str]:
    """Return the run IDs of every run in the named experiment."""
    return [
        run["run_id"] for run in state.get("runs", params={"experiment_name": name})
    ]


def delete_experiment(name: str) -> None:
    """Delete every run in the experiment, then the experiment itself.

    Its MLflow experiment is soft-deleted and keeps its name: MLflow never
    frees one, so `Experiment.init(name)` gives a new experiment of the same
    name an MLflow experiment under a suffixed name instead.

    Idempotent: an experiment cortexgrid has no record of is treated as
    success."""
    log.info("delete_experiment(%r): start", name)
    record = state.get("experiments", name)
    if record is None:
        log.info("delete_experiment(%r): early-exit, experiment not found", name)
        return
    run_ids = list_run_ids_in_experiment(name)
    log.info("delete_experiment(%r): %d run(s) to delete", name, len(run_ids))
    for run_id in run_ids:
        delete_run(run_id)
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    client.delete_experiment(record["mlflow_experiment_id"])
    state.delete("experiments", name)
    log.info("delete_experiment(%r): done", name)


def list_experiments() -> list[Experiment]:
    """Every run cortexgrid knows of, as (experiment_name, run_id) pairs."""
    return [
        Experiment(run["experiment_name"], run_id=run["run_id"])
        for run in state.get("runs")
    ]


def get_experiment_by_run_name(run_name: str) -> Experiment:
    """Resolve a run by its haikunator name back to its (experiment_name, run_id) pair."""
    runs = state.get("runs", params={"run_name": run_name})
    if not runs:
        raise ValueError(f"No run named {run_name!r}")
    return Experiment(
        experiment_name=runs[0]["experiment_name"], run_id=runs[0]["run_id"]
    )
