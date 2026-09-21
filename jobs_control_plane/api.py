"""The control plane's state API: the server side of cortexgrid/state.py.

Thin by design - every route is one jobs_control_plane.db call, and all the
logic stays in the cortexgrid library. A record that does not exist is a
404, and so is a write under a run cortexgrid has no record of.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from psycopg.errors import ForeignKeyViolation
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from jobs_control_plane import db


app = FastAPI(title="cortexgrid jobs control plane")


@app.exception_handler(ForeignKeyViolation)
def _missing_parent(_: Request, exc: ForeignKeyViolation) -> Response:
    return Response(status_code=404, content=str(exc))


def _found(record: Any) -> Any:
    if record is None:
        raise HTTPException(status_code=404)
    return record


class ExperimentBody(BaseModel):
    mlflow_experiment_id: str


class RunBody(BaseModel):
    run_name: str
    experiment_name: str


class ImportedModelBody(BaseModel):
    created_at: str


class LifecycleBody(BaseModel):
    """A cortexgrid.jobs.JobLifecycle, as `dataclasses.asdict` renders it."""

    experiment_name: str
    run_id: str
    job_id: str
    stop_requested: bool = False
    retry: bool = False
    num_gpus: int = 0
    num_cpus: int = 1
    pip_requirements: list[str] = []
    history: list[dict[str, Any]] = []


class ManifestBody(BaseModel):
    code_tarball_uri: str


class ModelBody(BaseModel):
    run_id: str | None
    source: str
    tags: dict[str, str]


class ObservationBody(BaseModel):
    """What the Serve controller reports for a deployed model."""

    phase: str
    message: str
    replicas: list[dict[str, Any]]


class DeploymentBody(ObservationBody):
    spec: dict[str, Any]
    tiers: list[int]
    url: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Experiments and runs


@app.put("/experiments/{name}")
def put_experiment(name: str, body: ExperimentBody) -> dict[str, Any]:
    return db.put_experiment(name, body.mlflow_experiment_id)


@app.get("/experiments")
def list_experiments() -> list[dict[str, Any]]:
    return db.list_experiments()


@app.get("/experiments/{name}")
def get_experiment(name: str) -> dict[str, Any]:
    return _found(db.get_experiment(name))


@app.delete("/experiments/{name}")
def delete_experiment(name: str) -> None:
    db.delete_experiment(name)


@app.put("/runs/{run_id}")
def put_run(run_id: str, body: RunBody) -> None:
    db.put_run(run_id, body.run_name, body.experiment_name)


@app.get("/runs")
def list_runs(
    experiment_name: str | None = None, run_name: str | None = None
) -> list[dict[str, Any]]:
    return db.list_runs(experiment_name, run_name)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _found(db.get_run(run_id))


@app.delete("/runs/{run_id}")
def delete_run(run_id: str) -> None:
    db.delete_run(run_id)


@app.post("/runs/{run_id}/stop")
def stop_run(run_id: str) -> None:
    db.stop_run(run_id)


@app.put("/runs/{run_id}/imported-models/{family}/{suffix}")
def put_imported_model(
    run_id: str, family: str, suffix: str, body: ImportedModelBody
) -> None:
    db.put_imported_model(run_id, family, suffix, body.created_at)


# Jobs


@app.get("/jobs/open")
def list_open_jobs() -> list[dict[str, Any]]:
    return db.list_open_jobs()


@app.get("/runs/{run_id}/jobs")
def list_jobs(run_id: str) -> list[dict[str, Any]]:
    return db.list_jobs(run_id)


@app.put("/runs/{run_id}/jobs/{job_id}")
def put_job(run_id: str, job_id: str, body: LifecycleBody) -> None:
    db.put_job({**body.model_dump(), "run_id": run_id, "job_id": job_id})


@app.get("/runs/{run_id}/jobs/{job_id}")
def get_job(run_id: str, job_id: str) -> dict[str, Any]:
    return _found(db.get_job(run_id, job_id))


@app.put("/runs/{run_id}/jobs/{job_id}/manifest")
def put_manifest(run_id: str, job_id: str, body: ManifestBody) -> None:
    db.put_manifest(run_id, job_id, body.code_tarball_uri)


@app.get("/runs/{run_id}/jobs/{job_id}/manifest")
def get_manifest(run_id: str, job_id: str) -> dict[str, Any]:
    return _found(db.get_manifest(run_id, job_id))


@app.put("/runs/{run_id}/jobs/{job_id}/result")
async def put_result(run_id: str, job_id: str, request: Request) -> None:
    # Raw bytes, so the handler is async to read the body; the write goes to
    # the threadpool like every other route's.
    await run_in_threadpool(db.put_result, run_id, job_id, await request.body())


@app.get("/runs/{run_id}/jobs/{job_id}/result")
def get_result(run_id: str, job_id: str) -> Response:
    return Response(
        _found(db.get_result(run_id, job_id)), media_type="application/octet-stream"
    )


@app.put("/runs/{run_id}/checkpoints/{prefix:path}")
def put_checkpoint(run_id: str, prefix: str, body: dict[str, Any]) -> None:
    db.put_checkpoint(run_id, prefix, body)


@app.get("/runs/{run_id}/checkpoints/{prefix:path}")
def get_checkpoint(run_id: str, prefix: str) -> dict[str, Any]:
    return _found(db.get_checkpoint(run_id, prefix))


# Model registry


@app.get("/models")
def list_models(run_id: str | None = None) -> list[dict[str, Any]]:
    return db.list_models(run_id)


@app.delete("/models")
def delete_models_for_run(run_id: str) -> None:
    db.delete_models_for_run(run_id)


@app.put("/models/{family}/{suffix}/{run_name}")
def put_model(family: str, suffix: str, run_name: str, body: ModelBody) -> None:
    db.put_model(family, suffix, run_name, body.run_id, body.source, body.tags)


@app.get("/models/{family}/{suffix}/{run_name}")
def get_model(family: str, suffix: str, run_name: str) -> dict[str, Any]:
    return _found(db.get_model(family, suffix, run_name))


@app.patch("/models/{family}/{suffix}/{run_name}/tags")
def patch_model_tags(
    family: str, suffix: str, run_name: str, body: dict[str, str]
) -> None:
    if not db.patch_model_tags(family, suffix, run_name, body):
        raise HTTPException(status_code=404)


@app.delete("/models/{family}/{suffix}/{run_name}")
def delete_model(family: str, suffix: str, run_name: str) -> None:
    db.delete_model(family, suffix, run_name)


# Deployments


@app.get("/deployments")
def list_deployments() -> list[dict[str, Any]]:
    return db.list_deployments()


@app.put("/deployments/{family}/{suffix}/{run_name}")
def put_deployment(
    family: str, suffix: str, run_name: str, body: DeploymentBody
) -> None:
    db.put_deployment(
        family,
        suffix,
        run_name,
        body.spec,
        body.tiers,
        body.url,
        body.phase,
        body.message,
        body.replicas,
    )


@app.get("/deployments/{family}/{suffix}/{run_name}")
def get_deployment(family: str, suffix: str, run_name: str) -> dict[str, Any]:
    return _found(db.get_deployment(family, suffix, run_name))


@app.patch("/deployments/{family}/{suffix}/{run_name}")
def observe_deployment(
    family: str, suffix: str, run_name: str, body: ObservationBody
) -> None:
    if not db.observe_deployment(
        family, suffix, run_name, body.phase, body.message, body.replicas
    ):
        raise HTTPException(status_code=404)


@app.delete("/deployments/{family}/{suffix}/{run_name}")
def delete_deployment(family: str, suffix: str, run_name: str) -> None:
    db.delete_deployment(family, suffix, run_name)
