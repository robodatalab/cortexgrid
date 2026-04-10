from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings

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
    name: str
    description: str
    port: int


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dashboards")
def dashboards() -> list[Dashboard]:
    return [
        Dashboard(
            id="mlflow",
            name="MLflow",
            description="Experiment tracking & model registry",
            port=settings.mlflow_port,
        ),
        Dashboard(
            id="ray",
            name="Ray",
            description="Distributed compute & jobs dashboard",
            port=settings.ray_port,
        ),
        Dashboard(
            id="minio",
            name="MinIO",
            description="S3-compatible object storage console",
            port=settings.minio_port,
        ),
    ]


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
