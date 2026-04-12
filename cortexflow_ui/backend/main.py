from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortexflow.experiment import Experiment, list_experiments
from cortexflow.jobs import list_experiment_jobs
from cortexflow.mlflow_util import (
    get_metric_history,
    list_run_artifacts,
    list_run_metrics,
    list_run_params,
)
from cortexflow.secrets import get_secret

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
def experiments() -> list[dict]:
    result = []
    for exp in list_experiments():
        jobs = list_experiment_jobs(exp)
        result.append({
            "experiment_name": exp.experiment_name,
            "run_id": exp.run_id,
            "run_name": exp.run_name(),
            "jobs": [{"job_id": j.job_id, "status": j.status.value} for j in jobs],
        })
    return result


@app.get("/api/runs/{run_id}/metrics")
def run_metrics(run_id: str) -> list[str]:
    return list_run_metrics(run_id)


@app.get("/api/runs/{run_id}/metrics/{key}")
def run_metric_history(run_id: str, key: str) -> list[dict]:
    return get_metric_history(run_id, key)


@app.get("/api/runs/{run_id}/params")
def run_params(run_id: str) -> dict[str, str]:
    return list_run_params(run_id)


@app.get("/api/runs/{run_id}/artifacts")
def run_artifacts(run_id: str) -> list[str]:
    return list_run_artifacts(run_id)


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
