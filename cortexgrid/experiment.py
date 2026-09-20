from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from cortexgrid.infra import get_mlflow_tracking_uri
from cortexgrid.jobs import request_run_jobs_deletion
from cortexgrid.model_storage import delete_models_for_run
from haikunator import Haikunator  # type: ignore
from mlflow.entities import Experiment as MlflowExperiment
from mlflow.entities import Run as MlflowRun
from mlflow.tracking import MlflowClient


log = logging.getLogger(__name__)
_SINGLETON_EXPERIMENT: Experiment | None = None

# Written on a run or an experiment that has been asked to go away. It is
# what keeps such a record out of every by-name lookup while the control
# plane, the one thing that reads it on purpose, tears the record down.
DELETE_REQUESTED_TAG = "cortexgrid.delete_requested"


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
        """Return cortexgrid job IDs submitted against this experiment+run."""
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
    # An experiment that asked to be deleted is invisible to the lookup
    # below, so a new one would quietly take its name while the control
    # plane is still tearing the old one down — and any run created here
    # would be torn down with it. Refuse instead.
    pending = get_experiment_by_name(experiment, include_deleting=True)
    if pending is not None and _pending_deletion(pending.tags):
        raise RuntimeError(
            f"Experiment {experiment!r} is being deleted; wait for the "
            "control plane to finish before creating it again"
        )
    # get_experiment_by_name also returns deleted experiments. A deleted one
    # cannot take new runs, so a fresh experiment is created under its name.
    experiment_obj = get_experiment_by_name(experiment)
    if experiment_obj is not None and experiment_obj.lifecycle_stage != "active":
        _release_deleted_experiment_name(client, experiment_obj)
        experiment_obj = None
    if experiment_obj:
        experiment_id = experiment_obj.experiment_id
    else:
        experiment_id = client.create_experiment(name=experiment)

    run_name = name_gen.haikunate(token_length=2, token_chars="0123456789")
    run = client.create_run(experiment_id=experiment_id, run_name=run_name)

    return (experiment, run.info.run_id)


def _deleted_experiment_name(name: str, experiment_id: str) -> str:
    """The name a deleted experiment is moved to, freeing `name` for reuse.
    Unique because experiment ids are."""
    return f"{name}__deleted__{experiment_id}"


def _release_deleted_experiment_name(
    client: MlflowClient, experiment: MlflowExperiment
) -> None:
    """Move a deleted experiment off its name, so a new experiment can take it.

    MLflow keeps a deleted experiment's name reserved (experiment names are
    unique across every lifecycle stage) and refuses to rename a deleted
    experiment, so it is restored only for the rename and deleted again."""
    log.info(
        "Experiment %r (id %s) is deleted; renaming it to release the name",
        experiment.name,
        experiment.experiment_id,
    )
    client.restore_experiment(experiment.experiment_id)
    client.rename_experiment(
        experiment.experiment_id,
        _deleted_experiment_name(experiment.name, experiment.experiment_id),
    )
    client.delete_experiment(experiment.experiment_id)


def delete_run(run_id: str) -> None:
    """Ask for a run, and everything under it, to be deleted.

    Intent only: every job in the run is latched and the run itself is
    tagged. The control plane is the only thing that acts on either — it
    stops the Ray attempts, wipes the job packages and artifacts, and
    removes the run once nothing is left under it.

    The tag takes the run out of every by-name lookup here, so a caller
    that deletes a run and immediately looks one up never gets the one on
    its way out. Models are not jobs and nothing else touches them, so
    they are deleted outright.
    """
    log.info("delete_run(%s): requesting deletion", run_id)
    request_run_jobs_deletion(run_id)
    delete_models_for_run(run_id)
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    client.set_tag(run_id, DELETE_REQUESTED_TAG, "true")


def list_run_ids_in_experiment(name: str) -> list[str]:
    """Return the run IDs of every active run in the named experiment."""
    exp = get_experiment_by_name(name)
    if exp is None:
        return []
    return [r.info.run_id for r in search_runs([exp.experiment_id])]


def delete_experiment(name: str) -> None:
    """Ask for an experiment, its runs and their jobs to be deleted.

    The experiment is renamed off `name` (`<name>__deleted__<id>`) as the
    last thing this does, so by the time the call returns the name is free
    and `Experiment.init(name)` creates a fresh experiment instead of
    attaching to the one being torn down. Everything else is intent: the
    control plane deletes each run once its jobs are gone, and the
    experiment once its runs are.

    Idempotent: an experiment that is absent, already deleted or already
    on its way out is treated as success."""
    log.info("delete_experiment(%r): requesting deletion", name)
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    exp = get_experiment_by_name(name)
    if exp is None:
        log.info("delete_experiment(%r): early-exit, experiment not found", name)
        return
    if exp.lifecycle_stage != "active":
        log.info(
            "delete_experiment(%r): early-exit, lifecycle=%s", name, exp.lifecycle_stage
        )
        return
    runs = search_runs([exp.experiment_id])
    log.info("delete_experiment(%r): %d run(s) to request", name, len(runs))
    for run in runs:
        delete_run(run.info.run_id)
    client.set_experiment_tag(exp.experiment_id, DELETE_REQUESTED_TAG, "true")
    client.rename_experiment(
        exp.experiment_id, _deleted_experiment_name(name, exp.experiment_id)
    )
    log.info("delete_experiment(%r): requested", name)


def _pending_deletion(tags: dict[str, str] | None) -> bool:
    return (tags or {}).get(DELETE_REQUESTED_TAG) == "true"


def search_experiments(include_deleting: bool = False) -> list[MlflowExperiment]:
    """Every experiment, minus the ones on their way out.

    This is the gate: reading experiments straight off MlflowClient
    bypasses the deletion rule and brings records back from the dead, so
    every reader — this module, the UI streams, the control plane — comes
    through here. Only the control plane passes `include_deleting=True`,
    because removing them is its job.
    """
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    return [
        e
        for e in client.search_experiments()
        if include_deleting or not _pending_deletion(e.tags)
    ]


def get_experiment_by_name(
    name: str, include_deleting: bool = False
) -> MlflowExperiment | None:
    """The experiment under `name`, unless it is on its way out.

    The gate for by-name resolution. Like MlflowClient's own call it still
    returns an experiment MLflow has deleted — `Experiment.init` has to see
    one to release its name — but a record that asked to be deleted is,
    to every caller but the control plane, already gone.
    """
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    exp = client.get_experiment_by_name(name)
    if exp is None:
        return None
    if not include_deleting and _pending_deletion(exp.tags):
        return None
    return exp


def search_runs(
    experiment_ids: list[str],
    include_deleting: bool = False,
    filter_string: str = "",
) -> list[MlflowRun]:
    """Every run of those experiments, minus the ones on their way out.

    The same gate, one level down, shaped like the client call it stands
    in front of so callers keep MLflow's server-side filtering. See
    `search_experiments`.
    """
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    return [
        r
        for r in client.search_runs(
            experiment_ids=experiment_ids, filter_string=filter_string
        )
        if include_deleting or not _pending_deletion(r.data.tags)
    ]


def list_experiments(include_deleting: bool = False) -> list[Experiment]:
    """Map MLflow experiment names to their run IDs.

    Records that asked to be deleted are left out: they are on their way
    to disappearing and must not be reachable any more. The control plane
    passes `include_deleting=True`, because making them disappear is its
    job and it cannot do it without seeing them."""
    result: list[Experiment] = []
    for exp in search_experiments(include_deleting):
        for run in search_runs([exp.experiment_id], include_deleting):
            result.append(Experiment(exp.name, run_id=run.info.run_id))
    return result


def runs_pending_deletion() -> list[str]:
    """Run ids tagged for deletion. The control plane is the only caller."""
    return [
        run.info.run_id
        for exp in search_experiments(include_deleting=True)
        for run in search_runs([exp.experiment_id], include_deleting=True)
        if _pending_deletion(run.data.tags)
    ]


def experiments_pending_deletion() -> list[str]:
    """Experiment ids tagged for deletion. The control plane is the only caller."""
    return [
        exp.experiment_id
        for exp in search_experiments(include_deleting=True)
        if _pending_deletion(exp.tags)
    ]


def experiment_has_active_runs(experiment_id: str) -> bool:
    """Whether anything is still under the experiment. Control plane only.

    Runs on their way out count: the experiment cannot go until they
    have actually gone."""
    return bool(search_runs([experiment_id], include_deleting=True))


def finish_run_deletion(run_id: str) -> None:
    """Remove a run whose jobs are gone. The control plane is the only caller."""
    log.info("finish_run_deletion(%s)", run_id)
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    client.delete_run(run_id)


def finish_experiment_deletion(experiment_id: str) -> None:
    """Remove an experiment whose runs are gone. Control plane only."""
    log.info("finish_experiment_deletion(%s)", experiment_id)
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    client.delete_experiment(experiment_id)


def get_experiment_by_run_name(run_name: str) -> Experiment:
    """Resolve a run by its haikunator name back to its (experiment_name, run_id) pair."""
    experiment_ids = [e.experiment_id for e in search_experiments()]
    if not experiment_ids:
        raise ValueError(f"No run named {run_name!r}")
    runs = search_runs(
        experiment_ids, filter_string=f"attributes.run_name = '{run_name}'"
    )
    if not runs:
        raise ValueError(f"No run named {run_name!r}")
    run = runs[0]
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    exp = client.get_experiment(run.info.experiment_id)
    return Experiment(experiment_name=exp.name, run_id=run.info.run_id)
