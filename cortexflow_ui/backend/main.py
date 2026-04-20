import logging
import os
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortexflow.experiment import list_experiments
from cortexflow.ray_util import (
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
    get_ray_job_id_for_cortexflow_job,
)
from cortexflow.infra import get_mlflow_run_url
from cortexflow.jobs import (
    list_experiment_run_jobs,
    stop_experiment_run_jobs,
    JobLifecycle,
)
from cortexflow.mlflow_util import (
    get_metric_history,
    list_run_artifacts,
    list_run_metrics,
    list_run_params,
)
from cortexflow.ray_util import get_ray_job_url, get_ray_logs
from cortexflow.secrets import (
    delete_secret,
    get_secret,
    list_secrets,
    set_secret,
)

from cortexflow_ui.backend.config import settings
from cortexflow_ui.backend.infra_status import InfraStatus, get_infra_status

log = logging.getLogger("cortexflow_ui_backend")

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


class Secret(BaseModel):
    id: str
    value: str


class SecretValue(BaseModel):
    value: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/infra/status")
def infra_status() -> InfraStatus:
    return get_infra_status()


@app.get("/api/dashboards")
def dashboards() -> list[Dashboard]:
    return [
        Dashboard(id="mlflow", url=os.environ["PUBLIC_MLFLOW_URL"]),
        Dashboard(id="ray", url=os.environ["PUBLIC_RAY_DASHBOARD_URL"]),
        Dashboard(id="minio", url=os.environ["PUBLIC_MINIO_CONSOLE_URL"]),
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


@app.get("/api/experiments")
def experiments() -> list[dict]:
    log.info("Listing experiments")
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    result = []
    for exp in list_experiments():
        jobs = []
        for job_id in exp.get_jobs():
            try:
                ray_job_id = get_ray_job_id_for_cortexflow_job(
                    exp.run_id, job_id, all_ray_submission_ids
                )
                status = get_ray_job_status(ray_job_id).value
            except Exception:
                status = "broken"
            jobs.append({"job_id": job_id, "status": status})
        result.append(
            {
                "experiment_name": exp.experiment_name,
                "run_id": exp.run_id,
                "run_name": exp.run_name(),
                "jobs": jobs,
            }
        )
    return result


@app.get("/api/runs/{run_id}/metrics")
def run_metrics(run_id: str) -> list[str]:
    return list_run_metrics(run_id)


@app.get("/api/runs/{run_id}/metrics/{key:path}")
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


@app.get("/api/runs/{run_id}/jobs")
def run_jobs(run_id: str) -> list[dict]:
    all_ray_submission_ids = list_ray_jobs_with_submission_id()
    return [
        {
            "job_id": j.job_id,
            "status": get_ray_job_status(
                j.get_ray_job_id(all_ray_submission_ids)
            ).value,
            "retry": j.retry,
        }
        for j in list_experiment_run_jobs(run_id)
    ]


def check_job_readiness(run_id: str, job_id: str) -> dict:
    code_entries = list_run_artifacts(run_id, f"job/{job_id}/project_code_root")
    code_ready = len(code_entries) > 0
    job_entries = list_run_artifacts(run_id, f"job/{job_id}")
    lifecycle_ready = any(Path(p).name == "lifecycle.json" for p in job_entries)
    return {
        "code": code_ready,
        "lifecycle": lifecycle_ready,
        "lifecycle_error": None if lifecycle_ready else "lifecycle.json not uploaded",
    }


@app.get("/api/runs/{run_id}/jobs/{job_id}")
def job_detail(run_id: str, job_id: str) -> dict:
    readiness = check_job_readiness(run_id, job_id)
    if not readiness["lifecycle"]:
        return {"job_id": job_id, "readiness": readiness}
    lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)
    ray_job_id = lifecycle.get_ray_job_id()

    history = [asdict(event) for event in lifecycle.history]
    for event in history:
        event["ray_url"] = get_ray_job_url(event["ray_job_id"])

    return {
        "job_id": lifecycle.job_id,
        "readiness": readiness,
        "status": get_ray_job_status(ray_job_id).value,
        "retry": lifecycle.retry,
        "stop_requested": lifecycle.stop_requested,
        "history": history,
    }


@app.get("/api/ray/jobs/{ray_job_id}/logs")
def ray_job_logs(ray_job_id: str) -> dict[str, str]:
    logs = get_ray_logs(ray_job_id)
    return {"logs": logs or ""}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, str]:
    stop_experiment_run_jobs(run_id)
    return {"status": "ok"}


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
