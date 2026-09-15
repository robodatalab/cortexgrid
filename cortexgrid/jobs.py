"""Submit tasks to the cortexgrid control plane."""

from __future__ import annotations

import cloudpickle  # type: ignore
from dataclasses import asdict, dataclass, field
import inspect
import io
import json
import logging
from pathlib import Path
import sys
import tarfile
import tempfile
from typing import Any, Callable

from cortexgrid import s3_util
from cortexgrid._bundle import bundle, stage, worker_provides
from cortexgrid.infra import get_mlflow_tracking_uri
from cortexgrid.ray_util import get_ray_job_id_for_cortexgrid_job
from haikunator import Haikunator  # type: ignore
from mlflow.tracking import MlflowClient
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)


@dataclass
class LifecycleEvent:
    """A single observed state on a given attempt of a job.

    ``start`` and ``end`` are ISO 8601 timestamps. ``end`` is ``None``
    while the state is still current; it is set when a subsequent
    observation shows the (attempt, state) pair has changed.
    ``ray_job_id`` is the Ray submission id observed at record time and
    is ``None`` for the pre-submission PENDING entry of a given attempt.
    ``error`` carries a worker-submission exception message and is
    attached to the event that was current when the worker failed.
    """

    attempt: int
    state: str
    start: str
    end: str | None = None
    ray_job_id: str | None = None
    error: str | None = None


@dataclass
class JobLifecycle:
    """Static identity and latches for a job.

    JobLifecycle is the source of truth for *job identity* and for a
    handful of fields that are either immutable or can only change once
    over the lifetime of a job. Live execution status is never stored
    here — it is derived on demand from Ray by :func:`get_ray_job_status`.
    """

    experiment_name: str
    run_id: str
    job_id: str
    stop_requested: bool = False  # latch: False -> True, never cleared
    retry: bool = False  # static flag set at job creation
    num_gpus: int = 0
    num_cpus: int = 1
    # static: pinned third-party requirements the worker pip-installs (the
    # bundle's distributions the Ray image does not already provide)
    pip_requirements: list[str] = field(default_factory=list)
    history: list[LifecycleEvent] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "JobLifecycle":
        data = json.loads(text)
        data.pop("error", None)
        data["history"] = [LifecycleEvent(**e) for e in data.get("history", [])]
        return cls(**data)

    def get_ray_job_id(
        self, all_ray_submission_ids: list[str] | None = None
    ) -> str | None:
        return get_ray_job_id_for_cortexgrid_job(
            self.run_id, self.job_id, all_ray_submission_ids
        )

    def download_project_code_root(self) -> str:
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        manifest_rel = f"job/{self.job_id}/manifest.json"
        if not any(
            a.path == manifest_rel
            for a in client.list_artifacts(self.run_id, f"job/{self.job_id}")
        ):
            raise FileNotFoundError(
                f"artifact {manifest_rel} not found in run {self.run_id}"
            )
        manifest_path = client.download_artifacts(self.run_id, manifest_rel)
        manifest = json.loads(Path(manifest_path).read_text())
        _, _, src_path = (
            manifest["code_tarball_uri"].removeprefix("s3://").partition("/")
        )
        extract_dir = Path(tempfile.mkdtemp())
        tarball_local = s3_util.download(
            src_path, local_path=str(extract_dir / "project_code_root.tar.gz")
        )
        with tarfile.open(tarball_local, "r:gz") as tar:
            tar.extractall(extract_dir)
        return str(extract_dir / "project_code_root")

    def save_to_mlflow(self) -> None:
        artifact_path = f"job/{self.job_id}"
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        log.info(
            "Saving lifecycle for job %s (stop_requested=%s)",
            self.job_id,
            self.stop_requested,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir, "lifecycle.json")
            local_path.write_text(self.to_json())
            client.log_artifact(
                self.run_id, str(local_path), artifact_path=artifact_path
            )

    @classmethod
    def load_from_mlflow(cls, run_id: str, job_id: str) -> "JobLifecycle":
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        lifecycle_rel = f"job/{job_id}/lifecycle.json"
        if not any(
            a.path == lifecycle_rel
            for a in client.list_artifacts(run_id, f"job/{job_id}")
        ):
            raise FileNotFoundError(
                f"artifact {lifecycle_rel} not found in run {run_id}"
            )
        local_path = client.download_artifacts(run_id, lifecycle_rel)
        return cls.from_json(Path(local_path).read_text())


class Payload(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    experiment_name: str
    run_id: str
    job_id: str
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    project_code_root: str
    num_gpus: int = 0
    num_cpus: int = 1

    def save_to_mlflow(self) -> None:
        artifact_path = f"job/{self.job_id}"
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        log.info(
            "Uploading payload for job %s from %s", self.job_id, self.project_code_root
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            tarball_path = Path(tmp_dir, "project_code_root.tar.gz")
            payload_bytes = cloudpickle.dumps(self)
            with tarfile.open(tarball_path, "w:gz") as tar:
                tar.add(self.project_code_root, arcname="project_code_root")
                info = tarfile.TarInfo("project_code_root/payload.pkl")
                info.size = len(payload_bytes)
                tar.addfile(info, io.BytesIO(payload_bytes))
            tarball_uri = s3_util.upload(
                str(tarball_path),
                dest_path=f"{artifact_path}/project_code_root.tar.gz",
            )
            manifest_path = Path(tmp_dir, "manifest.json")
            manifest_path.write_text(json.dumps({"code_tarball_uri": tarball_uri}))
            client.log_artifact(
                self.run_id, str(manifest_path), artifact_path=artifact_path
            )
            log.info("Payload upload complete for job %s", self.job_id)

    @classmethod
    def load_from_mlflow(cls, run_id: str, job_id: str) -> "Payload":
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        log.info("Downloading payload for job %s", job_id)
        manifest_rel = f"job/{job_id}/manifest.json"
        if not any(
            a.path == manifest_rel
            for a in client.list_artifacts(run_id, f"job/{job_id}")
        ):
            raise FileNotFoundError(
                f"artifact {manifest_rel} not found in run {run_id}"
            )
        manifest_path = client.download_artifacts(run_id, manifest_rel)
        manifest = json.loads(Path(manifest_path).read_text())
        _, _, src_path = (
            manifest["code_tarball_uri"].removeprefix("s3://").partition("/")
        )
        extract_dir = Path(tempfile.mkdtemp())
        tarball_local = s3_util.download(
            src_path, local_path=str(extract_dir / "project_code_root.tar.gz")
        )
        with tarfile.open(tarball_local, "r:gz") as tar:
            tar.extractall(extract_dir)
        project_code_root = str(extract_dir / "project_code_root")
        sys.path.insert(0, project_code_root)
        try:
            payload = cloudpickle.loads(
                Path(project_code_root, "payload.pkl").read_bytes()
            )
        finally:
            sys.path.remove(project_code_root)
        payload.project_code_root = project_code_root
        log.info(
            "Payload downloaded for job %s, project_code_root=%s",
            job_id,
            payload.project_code_root,
        )

        return payload


def schedule_remote_job(
    experiment_name: str,
    run_id: str,
    fn: Callable[..., Any],
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    retry: bool = False,
    **kwargs: Any,
) -> str:
    """Submit a function to the control plane. Returns a job ID."""
    job_id = Haikunator().haikunate(token_length=2, token_chars="0123456789")
    entry_file = Path(inspect.getfile(fn)).resolve()
    driver_file = Path(__file__).with_name("_ray_job_driver.py")
    desc = bundle(entry_file).merge(bundle(driver_file))
    pip_requirements = desc.pip_requirements(worker_provides())
    with tempfile.TemporaryDirectory() as tmp:
        code_root = Path(tmp, "project_code_root")
        stage(desc.local_files, code_root)
        log.info(
            "Submitting job %s (%d files, pip: %s)",
            job_id,
            len(desc.local_files),
            pip_requirements,
        )
        Payload(
            experiment_name=experiment_name,
            run_id=run_id,
            job_id=job_id,
            fn=fn,
            args=args,
            kwargs=kwargs,
            project_code_root=str(code_root),
            num_gpus=num_gpus,
            num_cpus=num_cpus,
        ).save_to_mlflow()
        JobLifecycle(
            experiment_name=experiment_name,
            run_id=run_id,
            job_id=job_id,
            retry=retry,
            num_gpus=num_gpus,
            num_cpus=num_cpus,
            pip_requirements=pip_requirements,
        ).save_to_mlflow()
    return job_id


def list_experiment_run_jobs(run_id: str) -> list[JobLifecycle]:
    """Return all jobs and their lifecycle states for this experiment+run."""
    client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
    entries = client.list_artifacts(run_id, path="job")
    result: list[JobLifecycle] = []
    for entry in entries:
        if not entry.is_dir:
            continue
        job_id = Path(entry.path).name
        try:
            result.append(JobLifecycle.load_from_mlflow(run_id, job_id))
        except Exception:
            logging.getLogger(__name__).warning(
                "Skipping job %s: missing lifecycle", job_id
            )
    return result


def stop_experiment_run_jobs(run_id: str) -> None:
    """Request all jobs in the run to stop by flipping the stop_requested latch.

    This function never touches Ray. The control plane observes the
    latch on its next poll and calls `ray.stop_job` for any job that
    has reached Ray. For jobs that have not yet been submitted, the
    latch short-circuits the submission path in the worker.

    Idempotent: already-requested jobs are skipped, and the flag has
    no effect on jobs that Ray already reports as terminal.
    """
    for job in list_experiment_run_jobs(run_id):
        if job.stop_requested:
            continue
        job.stop_requested = True
        job.save_to_mlflow()
