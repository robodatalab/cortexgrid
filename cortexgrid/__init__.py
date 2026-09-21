"""cortexgrid — connect your code to the RoboLab compute cluster.

import cortexgrid

# Fire-and-forget training on the DGX; returns a handle immediately.
# The jobs control plane picks up the submission and dispatches it to Ray.
training = cortexgrid.remote(my_train, config, num_gpus=1, retry=True)
print(f"Submitted: {training.job_id} ({training.status().value})")

# Or block on the function's return value, as if it had run locally.
# The job's own exception is what a failed job raises here.
job = cortexgrid.remote(score, batch, num_gpus=1)
loss = job.result(timeout=600)

# In another process, the job id is enough
loss = cortexgrid.get_job_result(job_id)

# Inside the training function — checkpoint after each epoch
with cortexgrid.checkpoint() as ckpt:
    ckpt.epoch = epoch
    ckpt.save_training_state(model, optimizer, scheduler)

# On resume — load checkpoint if it exists
ckpt = cortexgrid.resume()
if ckpt:
    ckpt.restore_training_state(model, optimizer, scheduler)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from cortexgrid import state
from cortexgrid.checkpoint import checkpoint, resume
from cortexgrid.experiment import (
    Experiment,
    delete_experiment,
    delete_run,
    list_experiments,
)
from cortexgrid.infra import get_ray_job_server_uri
from cortexgrid.jobs import (
    schedule_remote_job,
    list_experiment_run_jobs,
    stop_experiment_run_jobs,
    wait_for_job_result,
    JobFailed,
    JobFuture,
    JobLifecycle,
    JobResult,
    JobResultUnavailable,
    LifecycleEvent,
    Payload,
)
from cortexgrid.secrets import (
    delete_secret,
    get_secret,
    list_secrets,
    set_secret,
)
from cortexgrid.mlflow_util import (
    log_metric,
    log_metrics,
    log_params,
    log_artifact,
    get_mlflow_client,
    list_run_metrics,
    get_metric_history,
    list_run_params,
    list_run_artifacts,
)
from cortexgrid.ray_util import (
    get_ray_status,
    get_ray_logs,
    get_ray_job_url,
    stop_ray_job,
    submit_ray_job,
    list_ray_jobs_with_submission_id,
    get_ray_job_status,
    get_ray_job_id_for_cortexgrid_job,
    ray_submission_id,
    get_ray_job_attempt,
    JobStatus,
)
from cortexgrid.s3_util import delete_prefix, download, get_s3_client, upload, upload_dir
from cortexgrid.model_storage import (
    IMPORTED,
    NO_WEIGHTS,
    SavedModel,
    delete_model,
    list_models,
    load_model,
    model_config,
    model_registry_status,
    set_model_config,
    set_model_requirements,
)
from cortexgrid.model_storage import import_model as _import_model_storage
from cortexgrid.model_storage import register_model as _register_model_storage
from cortexgrid.model_storage import save_model as _save_model_storage
from cortexgrid.model_serving import (
    Deployment,
    ModelDeployFailed,
    ModelRequirements,
    ServingStatus,
    deploy_model,
    list_deployed_models,
    model_serving_status,
    undeploy_model,
    wait_for_model_serving,
)


def remote(
    fn: Callable[..., Any],
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    retry: bool = False,
    **kwargs: Any,
) -> JobFuture:
    """Submit a function to the control plane. Returns a handle on the job.

    The call does not block: the returned `JobFuture` carries the job id and
    offers `status()`, `done()` and `result()`. Only `result()` waits."""
    experiment = Experiment.get_instance()
    return schedule_remote_job(
        experiment.experiment_name,
        experiment.run_id,
        fn,
        *args,
        num_gpus=num_gpus,
        num_cpus=num_cpus,
        retry=retry,
        **kwargs,
    )


def get_job_result(job_id: str, timeout: float | None = None) -> Any:
    """Block until the job finishes, then return what its function returned.

    For a job of the current Experiment's run, known only by its id — the
    handle `remote` returned may live in another process. Raises whatever the
    job's function raised, so the call reads like a local one; see
    `wait_for_job_result` for the other errors and for `timeout`."""
    experiment = Experiment.get_instance()
    return wait_for_job_result(experiment.run_id, job_id, timeout)


def save_model(
    weights_dir: str | Path,
    serve_app: type,
    family: str,
    suffix: str,
    requirements: ModelRequirements | None = None,
    config: dict[str, str] | None = None,
) -> SavedModel:
    """Persist a weights directory under the current Experiment's run, paired
    with the serve-app class that will front it at deploy time, the hardware
    one replica of it needs, and any `config` the serve-app reads with
    `model_config`.

    Every run saves a new copy under its own run_name - meant for weights the
    run produced (e.g. a fine-tune). For a model produced elsewhere that should
    be uploaded once and reused across runs, use `import_model`."""
    experiment = Experiment.get_instance()
    return _save_model_storage(
        weights_dir,
        serve_app,
        suffix,
        family,
        run_id=experiment.run_id,
        run_name=experiment.run_name(),
        requirements=requirements,
        config=config,
    )


def import_model(
    source: str | Path | Callable[[], str | Path],
    serve_app: type,
    family: str,
    suffix: str,
    requirements: ModelRequirements | None = None,
    config: dict[str, str] | None = None,
) -> SavedModel:
    """Register a model produced elsewhere once, reuse it on every later call,
    and record on the current Experiment's run which imported model it used.

    The model belongs to no run (see `cortexgrid.model_storage.import_model`),
    so the run keeps the link instead: the run's record notes the model's
    `created_at` under (family, suffix), whether this call uploaded the model
    or reused it."""
    experiment = Experiment.get_instance()
    model = _import_model_storage(
        source, serve_app, family, suffix, requirements, config
    )
    _record_imported_model(experiment.run_id, family, suffix, model)
    return model


def register_model(
    serve_app: type,
    family: str,
    suffix: str,
    requirements: ModelRequirements | None = None,
    config: dict[str, str] | None = None,
) -> SavedModel:
    """Register a model with no weights of its own once - a serve-app that
    forwards to a hosted API stages nothing - reuse it on every later call, and
    record on the current Experiment's run which one it used.

    `import_model` without the import: everything it needs beyond its code goes
    in `config`, which the serve-app reads with `model_config` at construction
    (see `cortexgrid.model_storage.register_model`). The model belongs to no
    run, so the run keeps the link the same way."""
    experiment = Experiment.get_instance()
    model = _register_model_storage(
        serve_app, family, suffix, requirements, config
    )
    _record_imported_model(experiment.run_id, family, suffix, model)
    return model


def _record_imported_model(
    run_id: str, family: str, suffix: str, model: SavedModel
) -> None:
    """Note on the run's record which imported model it used."""
    state.put(
        "runs",
        run_id,
        "imported-models",
        family,
        suffix,
        body={"created_at": model.created_at},
    )


__all__ = [
    "Experiment",
    "delete_experiment",
    "delete_run",
    # Ray / jobs
    "remote",
    "get_job_result",
    "wait_for_job_result",
    "get_ray_job_status",
    "get_ray_job_id_for_cortexgrid_job",
    "list_experiment_run_jobs",
    "stop_experiment_run_jobs",
    "JobStatus",
    "JobFailed",
    "JobFuture",
    "JobLifecycle",
    "JobResult",
    "JobResultUnavailable",
    "LifecycleEvent",
    "Payload",
    "get_ray_status",
    "get_ray_logs",
    "get_ray_job_url",
    "stop_ray_job",
    "submit_ray_job",
    "get_ray_job_server_uri",
    "list_ray_jobs_with_submission_id",
    "ray_submission_id",
    "get_ray_job_attempt",
    # MLflow
    "log_metric",
    "log_metrics",
    "log_params",
    "log_artifact",
    "get_mlflow_client",
    "list_run_metrics",
    "get_metric_history",
    "list_run_params",
    "list_run_artifacts",
    "list_experiments",
    # Checkpointing
    "checkpoint",
    "resume",
    # S3
    "upload",
    "upload_dir",
    "download",
    "get_s3_client",
    "delete_prefix",
    # Secrets
    "get_secret",
    "set_secret",
    "list_secrets",
    "delete_secret",
    # Model registry
    "IMPORTED",
    "NO_WEIGHTS",
    "SavedModel",
    "save_model",
    "import_model",
    "register_model",
    "load_model",
    "list_models",
    "model_registry_status",
    "ModelRequirements",
    "set_model_requirements",
    "model_config",
    "set_model_config",
    "delete_model",
    # Model serving
    "Deployment",
    "ModelDeployFailed",
    "ServingStatus",
    "deploy_model",
    "wait_for_model_serving",
    "model_serving_status",
    "undeploy_model",
    "list_deployed_models",
]

# trigger: 315-trigger-tests-2026-05-16
