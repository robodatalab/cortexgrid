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
import time
import traceback
from typing import Any, Callable

from cortexgrid import s3_util
from cortexgrid._bundle import bundle, stage, worker_provides
from cortexgrid.infra import get_mlflow_tracking_uri
from cortexgrid.ray_util import (
    JobStatus,
    get_ray_job_id_for_cortexgrid_job,
    get_ray_job_status,
)
from haikunator import Haikunator  # type: ignore
from mlflow.tracking import MlflowClient
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

_JOB_POLL_INTERVAL_S = 5.0
_TERMINAL_JOB_STATES = (JobStatus.FINISHED, JobStatus.FAILED, JobStatus.STOPPED)


class JobFailed(RuntimeError):
    """A job did not produce a return value: its function raised, or the job
    ended in a terminal state before the function ever returned. Carries the
    remote traceback when there is one."""


class JobResultUnavailable(RuntimeError):
    """The job's function returned, but its value cannot be handed back here:
    it did not survive cloudpickle on the cluster, cannot be unpickled by this
    process, or was never recorded."""


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
    delete_requested: bool = False  # latch: False -> True, never cleared
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


def _try_dumps(obj: Any) -> tuple[bytes | None, str | None]:
    """cloudpickle ``obj``, or report why it could not be pickled.

    Returns ``(blob, None)`` on success and ``(None, reason)`` on failure.
    Nothing a job produces is allowed to turn into a job failure, so every
    pickling error is captured rather than raised."""
    try:
        return cloudpickle.dumps(obj), None
    except Exception as exc:
        return None, f"{type(obj).__name__} did not survive cloudpickle: {exc!r}"


@dataclass
class JobResult:
    """What a job's function returned or raised, recorded by the driver.

    Written to ``job/{job_id}/result.pkl``, beside the payload manifest and
    the lifecycle. The value and the exception are pickled separately from
    the envelope so an object that cannot be pickled costs only its own
    field: a job whose return value does not survive cloudpickle still
    finishes, and the reason is still readable here.
    """

    experiment_name: str
    run_id: str
    job_id: str
    ok: bool
    value_pickle: bytes | None = None  # cloudpickled return value
    value_error: str | None = None  # why value_pickle is None
    exception_pickle: bytes | None = None  # cloudpickled exception, when picklable
    traceback: str | None = None  # formatted remote traceback

    @classmethod
    def from_value(cls, payload: "Payload", value: Any) -> "JobResult":
        blob, error = _try_dumps(value)
        return cls(
            experiment_name=payload.experiment_name,
            run_id=payload.run_id,
            job_id=payload.job_id,
            ok=True,
            value_pickle=blob,
            value_error=error,
        )

    @classmethod
    def from_exception(cls, payload: "Payload", exc: BaseException) -> "JobResult":
        blob, _ = _try_dumps(exc)
        return cls(
            experiment_name=payload.experiment_name,
            run_id=payload.run_id,
            job_id=payload.job_id,
            ok=False,
            exception_pickle=blob,
            traceback="".join(traceback.format_exception(exc)),
        )

    def save_to_mlflow(self) -> None:
        artifact_path = f"job/{self.job_id}"
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        log.info("Recording result for job %s (ok=%s)", self.job_id, self.ok)
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = Path(tmp_dir, "result.pkl")
            local_path.write_bytes(cloudpickle.dumps(self))
            client.log_artifact(
                self.run_id, str(local_path), artifact_path=artifact_path
            )

    @classmethod
    def load_from_mlflow(cls, run_id: str, job_id: str) -> "JobResult":
        client = MlflowClient(tracking_uri=get_mlflow_tracking_uri())
        result_rel = f"job/{job_id}/result.pkl"
        if not any(
            a.path == result_rel for a in client.list_artifacts(run_id, f"job/{job_id}")
        ):
            raise FileNotFoundError(f"artifact {result_rel} not found in run {run_id}")
        local_path = client.download_artifacts(run_id, result_rel)
        result = cloudpickle.loads(Path(local_path).read_bytes())
        if not isinstance(result, cls):
            raise TypeError(f"{result_rel} in run {run_id} is not a JobResult")
        return result

    def unwrap(self) -> Any:
        """Return the value the job's function returned, or raise what it raised.

        A failed job raises the original exception, rebuilt from its pickle, so
        a blocking caller sees what a local call would have raised; the remote
        traceback rides along as the chained ``JobFailed`` cause. When the
        exception itself did not survive pickling, ``JobFailed`` is raised
        instead.
        """
        if not self.ok:
            failure = JobFailed(f"job {self.job_id} failed:\n{self.traceback}")
            remote_exc = self._unpickled_exception()
            if remote_exc is None:
                raise failure
            raise remote_exc from failure
        if self.value_pickle is None:
            raise JobResultUnavailable(
                f"job {self.job_id} finished, but its return value was not "
                f"recorded: {self.value_error}"
            )
        try:
            return cloudpickle.loads(self.value_pickle)
        except Exception as exc:
            raise JobResultUnavailable(
                f"job {self.job_id} finished, but its return value could not be "
                f"unpickled here: {exc!r}"
            ) from exc

    def _unpickled_exception(self) -> BaseException | None:
        """The remote exception object, or None when it cannot be rebuilt here."""
        if self.exception_pickle is None:
            return None
        try:
            exc = cloudpickle.loads(self.exception_pickle)
        except Exception:
            log.warning("Job %s: remote exception could not be unpickled", self.job_id)
            return None
        return exc if isinstance(exc, BaseException) else None


def _missing_result(
    lifecycle: JobLifecycle, ray_job_id: str | None, status: JobStatus
) -> Exception:
    """The error for a job that reached ``status`` leaving no result behind.

    Two ways in: the job's function never ran (the driver died, or the control
    plane could not hand the job to Ray), or it ran and the upload of its
    result failed. Either way the detail lives in the Ray logs, and a
    submission error lives on the lifecycle."""
    error = next((e.error for e in reversed(lifecycle.history) if e.error), None)
    detail = f": {error}" if error else f" (ray job {ray_job_id}; check its logs)"
    if status == JobStatus.FINISHED:
        return JobResultUnavailable(
            f"job {lifecycle.job_id} finished but recorded no result{detail}"
        )
    return JobFailed(
        f"job {lifecycle.job_id} ended in {status.value} before its function "
        f"returned{detail}"
    )


def wait_for_job_result(run_id: str, job_id: str, timeout: float | None = None) -> Any:
    """Block until the job reaches a terminal Ray state, then return what its
    function returned — or raise what it raised, as a local call would.

    Raises ``ValueError`` for a job submitted with ``retry=True``: retries are
    unbounded by design, so a blocking wait would sit through however many
    resubmissions the control plane makes. Raises ``TimeoutError`` once a
    finite ``timeout`` elapses (``timeout=0`` polls once and gives up);
    ``timeout=None`` has no deadline. Raises ``JobFailed`` if the job never
    got as far as returning, and ``JobResultUnavailable`` if it returned a
    value that cannot be handed back here.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)
        if lifecycle.retry:
            raise ValueError(
                f"job {job_id} was submitted with retry=True, which resubmits "
                "without bound; waiting on its result is not supported. Poll "
                "list_experiment_run_jobs instead."
            )
        ray_job_id = lifecycle.get_ray_job_id()
        status = get_ray_job_status(ray_job_id)
        if status in _TERMINAL_JOB_STATES:
            try:
                result = JobResult.load_from_mlflow(run_id, job_id)
            except FileNotFoundError:
                raise _missing_result(lifecycle, ray_job_id, status) from None
            return result.unwrap()
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(
                f"job {job_id} did not finish within {timeout}s "
                f"(last status={status.value})"
            )
        time.sleep(_JOB_POLL_INTERVAL_S)


@dataclass(frozen=True)
class JobFuture:
    """A handle on a submitted job: its identity, its live status, and its result.

    ``schedule_remote_job`` returns one as soon as the job request is recorded.
    Nothing here blocks until :meth:`result` is called.
    """

    experiment_name: str
    run_id: str
    job_id: str

    def status(self) -> JobStatus:
        """The job's live status, derived from Ray. ``PENDING`` covers both
        'the control plane has not picked it up yet' and 'queued in Ray'."""
        lifecycle = JobLifecycle.load_from_mlflow(self.run_id, self.job_id)
        return get_ray_job_status(lifecycle.get_ray_job_id())

    def done(self) -> bool:
        """True once Ray reports the job finished, failed or stopped."""
        return self.status() in _TERMINAL_JOB_STATES

    def result(self, timeout: float | None = None) -> Any:
        """Block until the job finishes and return its function's value.

        Raises exactly what :func:`wait_for_job_result` raises — including the
        job's own exception, so a blocking call reads like a local one."""
        return wait_for_job_result(self.run_id, self.job_id, timeout)


def schedule_remote_job(
    experiment_name: str,
    run_id: str,
    fn: Callable[..., Any],
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    retry: bool = False,
    **kwargs: Any,
) -> JobFuture:
    """Submit a function to the control plane. Returns a handle on the job."""
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
    return JobFuture(experiment_name=experiment_name, run_id=run_id, job_id=job_id)


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


def request_job_deletion(run_id: str, job_id: str) -> None:
    """Ask for one job to be deleted by flipping the delete_requested latch.

    This function never touches Ray, S3 or the job's artifacts: it
    records the intent on the job's own record and returns. The control
    plane observes the latch on its next poll and is the only thing that
    tears the job down, so a job created and deleted between two polls
    is simply never submitted.

    Idempotent: an already-requested job is left alone.
    """
    lifecycle = JobLifecycle.load_from_mlflow(run_id, job_id)
    if lifecycle.delete_requested:
        return
    lifecycle.delete_requested = True
    lifecycle.save_to_mlflow()


def request_run_jobs_deletion(run_id: str) -> None:
    """Ask for every job in the run to be deleted. See `request_job_deletion`."""
    for job in list_experiment_run_jobs(run_id):
        if job.delete_requested:
            continue
        job.delete_requested = True
        job.save_to_mlflow()
