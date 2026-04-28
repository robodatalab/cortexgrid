import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortexflow import s3_util
from cortexflow.experiment import get_mlflow_tracking_uri, list_experiments
from cortexflow.ray_util import (
    get_ray_job_status,
    list_ray_jobs_with_submission_id,
    get_ray_job_id_for_cortexflow_job,
)
from cortexflow.infra import get_mlflow_run_url, get_ray_job_server_uri
from cortexflow.jobs import (
    list_experiment_run_jobs,
    stop_experiment_run_jobs,
    JobLifecycle,
)
from cortexflow.mlflow_util import (
    get_metric_history,
    list_run_artifacts,
)
from mlflow.tracking import MlflowClient
from cortexflow.ray_util import get_ray_job_url, get_ray_logs
from cortexflow.secrets import (
    delete_secret,
    get_secret,
    list_secrets,
    set_secret,
)

from cortexflow_ui.backend.infra_status import InfraStatus, get_infra_status

log = logging.getLogger("cortexflow_ui_backend")

app = FastAPI(title="CortexFlow UI", version="0.1.0")

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


def _list_run_jobs(run_id: str) -> list[dict]:
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


@app.get("/api/runs/{run_id}/jobs")
def run_jobs(run_id: str) -> list[dict]:
    return _list_run_jobs(run_id)


@app.get("/api/runs/{run_id}/dashboard")
async def run_dashboard(run_id: str) -> dict:
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    run = await asyncio.to_thread(client.get_run, run_id)
    params = dict(run.data.params)
    metric_keys = list(run.data.metrics.keys())

    artifacts_task = asyncio.create_task(asyncio.to_thread(list_run_artifacts, run_id))
    jobs_task = asyncio.create_task(asyncio.to_thread(_list_run_jobs, run_id))
    metric_tasks = {
        key: asyncio.create_task(
            asyncio.to_thread(get_metric_history, run_id, key, max_points=500)
        )
        for key in metric_keys
    }

    metrics = {key: await task for key, task in metric_tasks.items()}
    artifacts = await artifacts_task
    jobs = await jobs_task

    return {
        "params": params,
        "metrics": metrics,
        "artifacts": artifacts,
        "url": get_mlflow_run_url(run_id),
        "jobs": jobs,
    }


def _tarball_exists(run_id: str, job_id: str) -> bool:
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    manifest_path = client.download_artifacts(run_id, f"job/{job_id}/manifest.json")
    manifest = json.loads(Path(manifest_path).read_text())
    bucket, _, key = manifest["code_tarball_uri"].removeprefix("s3://").partition("/")
    try:
        s3_util.get_s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


@app.get("/api/runs/{run_id}/jobs/{job_id}")
async def job_detail(run_id: str, job_id: str) -> dict:
    job_entries = await asyncio.to_thread(list_run_artifacts, run_id, f"job/{job_id}")
    lifecycle_ready = any(Path(p).name == "lifecycle.json" for p in job_entries)
    manifest_ready = any(Path(p).name == "manifest.json" for p in job_entries)

    tarball_task = (
        asyncio.create_task(asyncio.to_thread(_tarball_exists, run_id, job_id))
        if manifest_ready
        else None
    )
    lifecycle_task = (
        asyncio.create_task(asyncio.to_thread(JobLifecycle.load_from_mlflow, run_id, job_id))
        if lifecycle_ready
        else None
    )

    code_ready = bool(await tarball_task) if tarball_task else False
    readiness = {
        "code": code_ready,
        "lifecycle": lifecycle_ready,
        "lifecycle_error": None if lifecycle_ready else "lifecycle.json not uploaded",
    }
    if lifecycle_task is None:
        return {"job_id": job_id, "readiness": readiness}

    lifecycle = await lifecycle_task
    ray_job_id = lifecycle.get_ray_job_id()
    history = [asdict(event) for event in lifecycle.history]
    for event in history:
        event["ray_url"] = get_ray_job_url(event["ray_job_id"])
    status = await asyncio.to_thread(get_ray_job_status, ray_job_id)

    return {
        "job_id": lifecycle.job_id,
        "readiness": readiness,
        "status": status.value,
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
