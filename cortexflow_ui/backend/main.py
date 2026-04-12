from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortexflow.experiment import list_experiments
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
def experiments() -> list[dict[str, str]]:
    return [
        {"experiment_name": exp.experiment_name, "run_id": exp.run_id, "run_name": exp.run_name()}
        for exp in list_experiments()
    ]


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
