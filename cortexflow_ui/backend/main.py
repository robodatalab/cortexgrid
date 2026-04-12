from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortexflow.experiment import (
    Experiment,
    get_mlflow_run_url,
    list_experiments,
)
from cortexflow.jobs import get_job_status, list_experiment_jobs
from cortexflow.mlflow_util import (
    get_metric_history,
    list_run_artifacts,
    list_run_metrics,
    list_run_params,
)
from cortexflow.ray_util import get_ray_job_url, get_ray_logs, get_ray_status
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


@app.get("/api/runs/{run_id}/url")
def run_url(run_id: str) -> dict[str, str]:
    return {"url": get_mlflow_run_url(run_id)}


@app.get("/api/experiments/{experiment_name}/runs/{run_id}/jobs/{job_id}")
def job_detail(experiment_name: str, run_id: str, job_id: str) -> dict:
    exp = Experiment(experiment_name=experiment_name, run_id=run_id)
    lifecycle = get_job_status(exp, job_id)
    ray_status = get_ray_status(lifecycle.ray_job_id) if lifecycle.ray_job_id else None
    ray_url = get_ray_job_url(lifecycle.ray_job_id) if lifecycle.ray_job_id else None
    return {
        "job_id": lifecycle.job_id,
        "status": lifecycle.status.value,
        "error": lifecycle.error,
        "retry": lifecycle.retry,
        "ray_job_id": lifecycle.ray_job_id,
        "ray_status": ray_status,
        "ray_url": ray_url,
    }


@app.get("/api/ray/jobs/{ray_job_id}/logs")
def ray_job_logs(ray_job_id: str) -> dict[str, str]:
    return {"logs": get_ray_logs(ray_job_id)}


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
