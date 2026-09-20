import logging
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortexgrid.experiment import (
    delete_experiment,
    delete_run,
    get_experiment_by_run_name,
    get_mlflow_tracking_uri,
    list_run_ids_in_experiment,
)
from cortexgrid.infra import get_ray_job_server_uri
from cortexgrid.jobs import request_job_deletion, stop_experiment_run_jobs
from cortexgrid.model_serving import (
    ModelRequirements,
    ServingMessage,
    deploy_model,
    model_replica_placements,
    model_serving_messages,
    undeploy_model,
)
from cortexgrid.model_storage import (
    delete_model,
    set_model_config,
    set_model_requirements,
)
from cortexgrid.ray_util import get_ray_logs
from cortexgrid.secrets import (
    delete_secret,
    get_secret,
    list_secrets,
    set_secret,
)

from cortexgrid_ui.backend.models import (
    notes,
)
from cortexgrid_ui.backend.models.notes import (
    ExperimentNote,
    RunNote,
    delete_experiment_notes_for_experiment,
    delete_run_notes_for_run,
)
from cortexgrid_ui.backend.models.infra_status import (
    InfraStatus,
    PodStatus,
    device_for_ip,
    get_infra_status,
)
from cortexgrid_ui.backend.streams import (
    deployments_stream,
    experiment_notes_stream,
    experiments_stream,
    job_details_stream,
    jobs_stream,
    models_stream,
    run_dashboard_stream,
    run_jobs_stream,
    run_notes_stream,
)
from cortexgrid_ui.backend.utils.keyed_stream import (
    serve_websocket,
    serve_websocket_multi,
)

log = logging.getLogger("cortexgrid_ui_backend")

app = FastAPI(
    title="CortexGrid UI",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Dashboard(BaseModel):
    id: str
    url: str


class Secret(BaseModel):
    id: str
    value: str


class SecretValue(BaseModel):
    value: str


class NoteBody(BaseModel):
    body: str


class Requirements(BaseModel):
    """Hardware one replica of a model needs, as the dashboard edits it."""

    # Fractional: a share of a card, not only whole ones.
    num_gpus: float = 0.0
    ram_gb: float = 0.0
    vram_gb: float = 0.0


class ModelConfig(BaseModel):
    """Free-form settings the serve-app reads, as the dashboard edits them.
    The whole mapping replaces what is stored, so a key left out is removed."""

    config: dict[str, str] = {}


class RunByName(BaseModel):
    experiment_name: str
    run_id: str
    run_name: str


@app.on_event("startup")
async def _start_refreshers() -> None:
    for r in (
        experiments_stream.experiments_meta_refresher,
        experiments_stream.runs_refresher,
        run_jobs_stream.refresher,
        jobs_stream.refresher,
        run_dashboard_stream.refresher,
        job_details_stream.refresher,
        run_notes_stream.refresher,
        experiment_notes_stream.refresher,
        models_stream.models_refresher,
        deployments_stream.deployments_refresher,
    ):
        r.ensure_started()
    experiments_stream.experiments_meta_refresher.pin(experiments_stream.META_TOPIC)
    models_stream.models_refresher.pin(models_stream.META_TOPIC)
    deployments_stream.deployments_refresher.pin(deployments_stream.META_TOPIC)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/infra/status")
def infra_status() -> InfraStatus:
    return get_infra_status()


@app.get("/api/dashboards")
def dashboards() -> list[Dashboard]:
    return [
        Dashboard(id="mlflow", url=get_mlflow_tracking_uri()),
        Dashboard(id="ray", url=get_ray_job_server_uri()),
    ]


@app.get("/api/secrets")
def secrets() -> list[Secret]:
    return [Secret(id=sid, value=get_secret(sid)) for sid in list_secrets()]


@app.put("/api/secrets/{id}")
def secret_put(id: str, body: SecretValue) -> dict[str, str]:
    set_secret(id, body.value)
    return {"status": "ok"}


@app.delete("/api/secrets/{id}")
def secret_delete(id: str) -> dict[str, str]:
    delete_secret(id)
    return {"status": "ok"}


@app.websocket("/api/experiments/meta/stream")
async def experiments_meta_stream_endpoint(ws: WebSocket) -> None:
    await serve_websocket(
        experiments_stream.experiments_meta_refresher,
        ws,
        experiments_stream.META_TOPIC,
    )


@app.websocket("/api/models/stream")
async def models_stream_endpoint(ws: WebSocket) -> None:
    await serve_websocket(
        models_stream.models_refresher,
        ws,
        models_stream.META_TOPIC,
    )


@app.websocket("/api/deployments/stream")
async def deployments_stream_endpoint(ws: WebSocket) -> None:
    await serve_websocket(
        deployments_stream.deployments_refresher,
        ws,
        deployments_stream.META_TOPIC,
    )


@app.delete("/api/models/{family}/{suffix}/{run_name}")
async def model_delete(family: str, suffix: str, run_name: str) -> dict[str, str]:
    delete_model(family, suffix, run_name)
    await models_stream.models_refresher.remove(
        models_stream.META_TOPIC,
        models_stream.model_id(family, suffix, run_name),
    )
    return {"status": "ok"}


@app.put("/api/models/{family}/{suffix}/{run_name}/requirements")
async def model_requirements_update(
    family: str, suffix: str, run_name: str, body: Requirements
) -> dict[str, str]:
    try:
        requirements = ModelRequirements(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        set_model_requirements(family, suffix, run_name, requirements)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Push the edit into the stream so the dashboard shows it without waiting
    # for the next sweep.
    model_id = models_stream.model_id(family, suffix, run_name)
    cached = models_stream.models_cache.get(models_stream.META_TOPIC).get(model_id)
    if cached is not None:
        await models_stream.models_refresher.update_or_insert(
            models_stream.META_TOPIC,
            model_id,
            replace(cached, requirements=requirements),
        )
    return {"status": "ok"}


@app.put("/api/models/{family}/{suffix}/{run_name}/config")
async def model_config_update(
    family: str, suffix: str, run_name: str, body: ModelConfig
) -> dict[str, str]:
    # The same rule `set_model_config` enforces, checked here so a blank key
    # reads as a bad request rather than a missing model.
    if any(not key.strip() for key in body.config):
        raise HTTPException(status_code=400, detail="Config keys cannot be blank")
    try:
        set_model_config(family, suffix, run_name, body.config)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Push the edit into the stream so the dashboard shows it without waiting
    # for the next sweep.
    model_id = models_stream.model_id(family, suffix, run_name)
    cached = models_stream.models_cache.get(models_stream.META_TOPIC).get(model_id)
    if cached is not None:
        await models_stream.models_refresher.update_or_insert(
            models_stream.META_TOPIC,
            model_id,
            replace(cached, config=body.config),
        )
    return {"status": "ok"}


@app.post("/api/deployments/{family}/{suffix}/{run_name}")
def deployment_create(family: str, suffix: str, run_name: str) -> dict[str, str]:
    deploy_model(family, suffix, run_name)
    return {"status": "ok"}


@app.delete("/api/deployments/{family}/{suffix}/{run_name}")
def deployment_delete(family: str, suffix: str, run_name: str) -> dict[str, str]:
    undeploy_model(family, suffix, run_name)
    return {"status": "ok"}


@app.get("/api/deployments/{family}/{suffix}/{run_name}/messages")
def deployment_messages(
    family: str, suffix: str, run_name: str
) -> list[ServingMessage]:
    return model_serving_messages(family, suffix, run_name)


class ReplicaDevice(BaseModel):
    """One replica of a deployment and the machine serving it.

    `device` is the worker pod as the Infrastructure Status tab reports it, so
    the deployment card can show the same health verdict rather than inventing
    a second one. It is null for a replica Ray has not placed yet, and for one
    whose worker kubernetes no longer knows."""

    replica_id: str
    state: str
    node_ip: str | None = None
    device: PodStatus | None = None


@app.get("/api/deployments/{family}/{suffix}/{run_name}/devices")
def deployment_devices(
    family: str, suffix: str, run_name: str
) -> list[ReplicaDevice]:
    return [
        ReplicaDevice(
            replica_id=placement.replica_id,
            state=placement.state,
            node_ip=placement.node_ip,
            device=device_for_ip(placement.node_ip),
        )
        for placement in model_replica_placements(family, suffix, run_name)
    ]


@app.delete("/api/models/{family}")
async def model_family_delete(family: str) -> dict[str, str]:
    versions = list(models_stream.models_cache.get(models_stream.META_TOPIC).values())
    for m in versions:
        if m.family != family:
            continue
        delete_model(m.family, m.suffix, m.run_name)
        await models_stream.models_refresher.remove(
            models_stream.META_TOPIC, m.id
        )
    return {"status": "ok"}


@app.websocket("/api/experiments/{experiment_name}/runs/stream")
async def runs_stream_endpoint(ws: WebSocket, experiment_name: str) -> None:
    await serve_websocket(
        experiments_stream.runs_refresher, ws, experiment_name
    )


@app.websocket("/api/runs/{run_id}/jobs/stream")
async def run_jobs_stream_endpoint(ws: WebSocket, run_id: str) -> None:
    await serve_websocket(run_jobs_stream.refresher, ws, run_id)


@app.websocket("/api/jobs/stream")
async def jobs_stream_endpoint(ws: WebSocket) -> None:
    await serve_websocket(jobs_stream.refresher, ws, jobs_stream.META_TOPIC)


@app.delete("/api/runs/{run_id}/jobs/{job_id}")
async def job_delete(run_id: str, job_id: str) -> dict[str, str]:
    """Ask for a job to be deleted.

    Writes the latch and nothing else: the control plane stops the Ray
    attempts, wipes the package and removes the record. The row is pushed
    to `deleting` here rather than waiting for the next sweep, so the
    table reflects the request immediately.
    """
    try:
        request_job_deletion(run_id, job_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"No job {job_id!r} in run {run_id!r}"
        )
    row = jobs_stream.cache.get(jobs_stream.META_TOPIC).get(
        jobs_stream.row_id(run_id, job_id)
    )
    if row is not None:
        await jobs_stream.refresher.update_or_insert(
            jobs_stream.META_TOPIC, row.id, replace(row, status="deleting")
        )
    return {"status": "ok"}


@app.websocket("/api/runs/{run_name}/stream")
async def run_dashboard_stream_endpoint(ws: WebSocket, run_name: str) -> None:
    await serve_websocket(run_dashboard_stream.refresher, ws, run_name)


@app.websocket("/api/runs/{run_id}/jobs/{job_id}/stream")
async def job_details_stream_endpoint(ws: WebSocket, run_id: str, job_id: str) -> None:
    await serve_websocket(job_details_stream.refresher, ws, (run_id, job_id))


@app.get("/api/runs/by-name/{run_name}")
def run_by_name(run_name: str) -> RunByName:
    try:
        exp = get_experiment_by_run_name(run_name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No run named {run_name!r}")
    return RunByName(
        experiment_name=exp.experiment_name,
        run_id=exp.run_id,
        run_name=run_name,
    )


@app.get("/api/ray/jobs/{ray_job_id}/logs")
def ray_job_logs(ray_job_id: str) -> dict[str, str]:
    logs = get_ray_logs(ray_job_id)
    return {"logs": logs or ""}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, str]:
    stop_experiment_run_jobs(run_id)
    return {"status": "ok"}


@app.delete("/api/runs/{run_id}")
async def run_delete(run_id: str) -> dict[str, str]:
    delete_run_notes_for_run(run_id)
    delete_run(run_id)
    for exp_name, runs in list(experiments_stream.runs_cache._data.items()):
        for run_name, run in list(runs.items()):
            if run.run_id == run_id:
                await experiments_stream.runs_refresher.remove(
                    exp_name, run_name
                )
    return {"status": "ok"}


@app.delete("/api/experiments/{experiment_name}")
async def experiment_delete(experiment_name: str) -> dict[str, str]:
    for run_id in list_run_ids_in_experiment(experiment_name):
        delete_run_notes_for_run(run_id)
    delete_experiment_notes_for_experiment(experiment_name)
    delete_experiment(experiment_name)
    for run_name in list(
        experiments_stream.runs_cache.get(experiment_name).keys()
    ):
        await experiments_stream.runs_refresher.remove(experiment_name, run_name)
    await experiments_stream.experiments_meta_refresher.remove(
        experiments_stream.META_TOPIC, experiment_name
    )
    return {"status": "ok"}


@app.websocket("/api/runs/{run_name}/notes/stream")
async def run_notes_stream_endpoint(ws: WebSocket, run_name: str) -> None:
    await serve_websocket(run_notes_stream.refresher, ws, run_name)


@app.websocket("/api/experiments/{experiment_name}/notes/stream")
async def experiment_notes_stream_endpoint(ws: WebSocket, experiment_name: str) -> None:
    """Combined notes feed for an experiment.

    Multiplexes the experiment_notes_stream for `experiment_name` with
    the run_notes_stream for each run currently in the experiment.
    The set of runs is captured at connect time; runs added after the
    subscription opens are not picked up until the client reconnects.
    """
    run_names = experiments_stream.runs_for_experiment(experiment_name)
    subscriptions = [(experiment_notes_stream.refresher, experiment_name)]
    for run_name in run_names:
        subscriptions.append((run_notes_stream.refresher, run_name))
    await serve_websocket_multi(ws, subscriptions)


@app.post("/api/runs/{run_name}/notes")
async def add_run_note(run_name: str, body: NoteBody) -> RunNote:
    note = notes.add_run_note(run_name, body.body)
    if note is None:
        raise HTTPException(status_code=500, detail="failed to add run note")
    await run_notes_stream.refresher.update_or_insert(run_name, note.id, note)
    return note


@app.put("/api/notes/run/{note_id}")
async def update_run_note(note_id: str, body: NoteBody) -> RunNote:
    note = notes.update_run_note(note_id, body.body)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    await run_notes_stream.refresher.update_or_insert(note.run_name, note.id, note)
    return note


@app.delete("/api/notes/run/{note_id}")
async def delete_run_note(note_id: str) -> dict[str, str]:
    run_name = notes.delete_run_note(note_id)
    if run_name is None:
        raise HTTPException(status_code=404, detail="note not found")
    await run_notes_stream.refresher.remove(run_name, note_id)
    return {"status": "ok"}


@app.post("/api/experiments/{experiment_name}/notes")
async def add_experiment_note(experiment_name: str, body: NoteBody) -> ExperimentNote:
    note = notes.add_experiment_note(experiment_name, body.body)
    if note is None:
        raise HTTPException(status_code=500, detail="failed to add experiment note")
    await experiment_notes_stream.refresher.update_or_insert(experiment_name, note.id, note)
    return note


@app.put("/api/notes/experiment/{note_id}")
async def update_experiment_note(note_id: str, body: NoteBody) -> ExperimentNote:
    note = notes.update_experiment_note(note_id, body.body)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    await experiment_notes_stream.refresher.update_or_insert(
        note.experiment_name, note.id, note
    )
    return note


@app.delete("/api/notes/experiment/{note_id}")
async def delete_experiment_note(note_id: str) -> dict[str, str]:
    experiment_name = notes.delete_experiment_note(note_id)
    if experiment_name is None:
        raise HTTPException(status_code=404, detail="note not found")
    await experiment_notes_stream.refresher.remove(experiment_name, note_id)
    return {"status": "ok"}


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
