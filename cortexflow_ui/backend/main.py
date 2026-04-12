from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortexflow.experiment import Experiment, get_mlflow_tracking_uri, list_experiments
from cortexflow.secrets import get_secret
from mlflow.tracking import MlflowClient

from cortexflow_ui.backend.config import settings

app = FastAPI(title="CortexFlow UI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Dashboard(BaseModel):
    id: str
    url: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dashboards")
def dashboards() -> list[Dashboard]:
    host = get_secret("robolab/infra/DGX_TAILSCALE_IP")
    return [
        Dashboard(id="mlflow", url=f"http://{host}:{get_secret('robolab/infra/MLFLOW_PORT')}"),
        Dashboard(id="ray", url=f"http://{host}:{get_secret('robolab/infra/RAY_DASHBOARD_PORT')}"),
        Dashboard(id="minio", url=f"http://{host}:{get_secret('robolab/infra/MINIO_CONSOLE_PORT')}"),
    ]


@app.get("/api/experiments")
def experiments() -> list[dict[str, str]]:
    return [
        {"experiment_name": exp.experiment_name, "run_id": exp.run_id, "run_name": exp.run_name()}
        for exp in list_experiments()
    ]


@app.get("/api/experiments/{experiment_name}")
def experiment_detail(experiment_name: str) -> dict:
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    exp = client.get_experiment_by_name(experiment_name)
    if exp is None:
        return {"error": "not found"}
    runs = client.search_runs(experiment_ids=[exp.experiment_id])
    return {
        "experiment_name": exp.name,
        "experiment_id": exp.experiment_id,
        "lifecycle_stage": exp.lifecycle_stage,
        "creation_time": exp.creation_time,
        "last_update_time": exp.last_update_time,
        "run_count": len(runs),
        "tags": dict(exp.tags) if exp.tags else {},
    }


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
