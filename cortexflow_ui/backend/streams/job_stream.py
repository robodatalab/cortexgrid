"""Per-job detail stream.

Polls job readiness + lifecycle history + Ray status every
``JOB_STREAM_POLL_INTERVAL_SEC`` seconds while at least one WebSocket
subscriber is watching the (run_id, job_id) pair. Pushes the full
detail dict on every poll.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from cortexflow import s3_util
from cortexflow.experiment import get_mlflow_tracking_uri
from cortexflow.jobs import JobLifecycle
from cortexflow.mlflow_util import list_run_artifacts
from cortexflow.ray_util import get_ray_job_status, get_ray_job_url
from cortexflow_ui.backend.streams.config import JOB_STREAM_POLL_INTERVAL_SEC
from cortexflow_ui.backend.keyed_stream import KeyedStream
from mlflow.tracking import MlflowClient


def tarball_exists(run_id: str, job_id: str) -> bool:
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    manifest_path = client.download_artifacts(run_id, f"job/{job_id}/manifest.json")
    manifest = json.loads(Path(manifest_path).read_text())
    bucket, _, key = manifest["code_tarball_uri"].removeprefix("s3://").partition("/")
    try:
        s3_util.get_s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def poll_job(key: tuple[str, str]) -> dict:
    run_id, job_id = key
    job_entries = list_run_artifacts(run_id, f"job/{job_id}")
    lifecycle_ready = any(Path(p).name == "lifecycle.json" for p in job_entries)
    manifest_ready = any(Path(p).name == "manifest.json" for p in job_entries)
    code_ready = manifest_ready and tarball_exists(run_id, job_id)
    readiness = {
        "code": code_ready,
        "lifecycle": lifecycle_ready,
        "lifecycle_error": None if lifecycle_ready else "lifecycle.json not uploaded",
    }
    if not lifecycle_ready:
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


stream = KeyedStream(
    name="job_stream",
    poll_fn=poll_job,
    poll_interval_sec=JOB_STREAM_POLL_INTERVAL_SEC,
)
