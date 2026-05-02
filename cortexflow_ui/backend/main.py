import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortexflow.experiment import get_mlflow_tracking_uri
from cortexflow.infra import get_ray_job_server_uri
from cortexflow.jobs import stop_experiment_run_jobs
from cortexflow.ray_util import get_ray_logs
from cortexflow.secrets import (
    delete_secret,
    get_secret,
    list_secrets,
    set_secret,
)

from cortexflow_ui.backend.models import (
    notes,
)
from cortexflow_ui.backend.models.notes import ExperimentNote, RunNote
from cortexflow_ui.backend.models.infra_status import InfraStatus, get_infra_status
from cortexflow_ui.backend.streams import (
    experiment_notes_stream,
    experiments_stream,
    job_details_stream,
    run_dashboard_stream,
    run_jobs_stream,
    run_notes_stream,
)
from cortexflow_ui.backend.utils.keyed_stream import (
    serve_websocket,
    serve_websocket_multi,
)

log = logging.getLogger("cortexflow_ui_backend")

app = FastAPI(
    title="CortexFlow UI",
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


@app.on_event("startup")
async def _start_refreshers() -> None:
    for r in (
        experiments_stream.refresher,
        run_jobs_stream.refresher,
        run_dashboard_stream.refresher,
        job_details_stream.refresher,
        run_notes_stream.refresher,
        experiment_notes_stream.refresher,
    ):
        r.ensure_started()
    experiments_stream.refresher.pin(experiments_stream.TOPIC)


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


@app.websocket("/api/experiments/stream")
async def experiments_stream_endpoint(ws: WebSocket) -> None:
    await serve_websocket(experiments_stream.refresher, ws, experiments_stream.TOPIC)


@app.websocket("/api/runs/{run_id}/jobs/stream")
async def run_jobs_stream_endpoint(ws: WebSocket, run_id: str) -> None:
    await serve_websocket(run_jobs_stream.refresher, ws, run_id)


@app.websocket("/api/runs/{run_name}/stream")
async def run_dashboard_stream_endpoint(ws: WebSocket, run_name: str) -> None:
    await serve_websocket(run_dashboard_stream.refresher, ws, run_name)


@app.websocket("/api/runs/{run_id}/jobs/{job_id}/stream")
async def job_details_stream_endpoint(ws: WebSocket, run_id: str, job_id: str) -> None:
    await serve_websocket(job_details_stream.refresher, ws, (run_id, job_id))


@app.get("/api/ray/jobs/{ray_job_id}/logs")
def ray_job_logs(ray_job_id: str) -> dict[str, str]:
    logs = get_ray_logs(ray_job_id)
    return {"logs": logs or ""}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, str]:
    stop_experiment_run_jobs(run_id)
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
