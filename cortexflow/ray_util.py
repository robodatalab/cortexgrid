"""Ray wrappers.

cortexflow.remote — wraps a function for execution on the DGX via the Ray Jobs API
cortexflow.get    — waits for submitted jobs and returns results
cortexflow.status — check job status without blocking
cortexflow.result — retrieve result of a completed job
cortexflow.logs   — fetch logs from a running or completed job

Uses the Ray Jobs API (HTTP to :8265). The Mac never joins the cluster.
Jobs run on the DGX and survive laptop disconnection.
"""

from __future__ import annotations

import base64
import cloudpickle  # type: ignore
import pickle
import textwrap
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ray.job_submission import JobSubmissionClient, JobStatus
from tqdm import tqdm  # type: ignore

from cortexflow.config import get_config
from cortexflow.project import build_runtime_env


@dataclass
class JobInfo:
    """Status information for a submitted job."""

    job_id: str
    status: str
    message: str | None = None


class Job:
    """Handle to a submitted Ray job.

    The ``job_id`` can be saved and used later with ``cortexflow.status()``,
    ``cortexflow.result()``, or ``cortexflow.logs()``.
    """

    def __init__(
        self, client: Any, job_id: str, cortexflow_job_id: str | None = None
    ) -> None:
        self.client = client
        self.job_id = job_id
        self.cortexflow_job_id = cortexflow_job_id

    def __repr__(self) -> str:
        return f"Job({self.job_id!r})"


class _RemoteFunction:
    """A function wrapped for submission to the DGX via the Ray Jobs API."""

    def __init__(
        self,
        fn: Any,
        num_gpus: int,
        max_retries: int,
        runtime_env: dict[str, Any],
    ) -> None:
        self._fn = fn
        self._num_gpus = num_gpus
        self._max_retries = max_retries
        self._runtime_env = runtime_env

    def remote(self, *args: Any, **kwargs: Any) -> Job:
        """Submit this function to the Ray cluster. Returns a Job handle."""
        config = get_config()
        client = JobSubmissionClient(f"http://{config.dgx_ip}:8265")

        cortexflow_job_id = str(uuid.uuid4())

        payload = base64.b64encode(
            cloudpickle.dumps(
                {
                    "fn": self._fn,
                    "args": args,
                    "kwargs": kwargs,
                    "num_gpus": self._num_gpus,
                    "max_retries": self._max_retries,
                    "cortexflow_job_id": cortexflow_job_id,
                }
            )
        ).decode()

        # Driver script that runs inside the Ray cluster.
        # Includes retry logic so the job survives transient failures
        # (OOM, CUDA errors) even after the submitting laptop disconnects.
        # Each retry re-invokes the user function from the top; the
        # function can call cortexflow.resume() to reload checkpoints.
        driver = textwrap.dedent("""\
            import base64, os, pickle, sys, time, traceback
            import ray

            payload = pickle.loads(base64.b64decode(sys.argv[1]))
            fn = payload["fn"]
            args = payload["args"]
            kwargs = payload["kwargs"]
            max_retries = payload.get("max_retries", 0)
            cortexflow_job_id = payload["cortexflow_job_id"]

            os.environ["CORTEXFLOW_JOB_ID"] = cortexflow_job_id
            print(f"__CORTEXFLOW_JOB_ID__:{cortexflow_job_id}")

            ray.init()

            remote_fn = ray.remote(
                num_gpus=payload["num_gpus"],
                max_retries=0,
                runtime_env={"env_vars": {"CORTEXFLOW_JOB_ID": cortexflow_job_id}},
            )(fn)

            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    if attempt > 0:
                        delay = min(2 ** attempt, 60)
                        print(f"[cortexflow] Retry {attempt}/{max_retries} after {delay}s")
                        time.sleep(delay)
                    result = ray.get(remote_fn.remote(*args, **kwargs))
                    result_bytes = base64.b64encode(pickle.dumps(result)).decode()
                    print(f"__CORTEXFLOW_RESULT__:{result_bytes}")
                    sys.exit(0)
                except Exception as exc:
                    last_exc = exc
                    traceback.print_exc()
                    print(f"[cortexflow] Attempt {attempt + 1}/{max_retries + 1} failed: {exc}")

            print(f"[cortexflow] All {max_retries + 1} attempts failed")
            sys.exit(1)
        """)

        entrypoint = f"python -u -c {_shell_quote(driver)} {payload}"

        ray_job_id = client.submit_job(
            entrypoint=entrypoint,
            runtime_env=self._runtime_env,
            metadata={"cortexflow_job_id": cortexflow_job_id},
        )

        return Job(client, ray_job_id, cortexflow_job_id=cortexflow_job_id)


def remote(
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    max_retries: int = 0,
    retry_exceptions: bool = False,
    **kwargs: Any,
) -> Any:
    """Wrap a function for remote execution on the DGX cluster.

    ``max_retries`` controls how many times the driver re-invokes the
    function on the DGX if it raises.  Combined with
    ``cortexflow.checkpoint`` / ``cortexflow.resume``, each retry
    resumes from the latest checkpoint.

    Usage (fire-and-forget)::

        train_fn = cortexflow.remote(num_gpus=1, max_retries=3)(my_function)
        job = train_fn.remote(arg1, arg2)
        print(f"Submitted: {job.job_id}")

    Usage (blocking)::

        [result] = cortexflow.get([job])
    """
    config = get_config()
    runtime_env = build_runtime_env(github_token=config.github_token)
    env_vars = config.env_vars_for_job()
    if env_vars:
        existing = runtime_env.get("env_vars", {})
        existing.update(env_vars)
        runtime_env["env_vars"] = existing

    def decorator(fn: Any) -> _RemoteFunction:
        return _RemoteFunction(
            fn=fn,
            num_gpus=num_gpus,
            max_retries=max_retries,
            runtime_env=runtime_env,
        )

    # Support @cortexflow.remote (bare, no parens)
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return decorator(args[0])
    return decorator


def status(job_id: str) -> JobInfo:
    """Check the status of a previously submitted job.

    Works from any process -- only needs the job_id string.
    """
    client = get_ray_client()
    st = client.get_job_status(job_id)
    info = client.get_job_info(job_id)
    return JobInfo(
        job_id=job_id,
        status=st.value,
        message=info.message if info else None,
    )


def result(job_id: str) -> Any:
    """Retrieve the return value of a completed job.

    Raises ``RuntimeError`` if the job hasn't finished or failed.
    """
    client = get_ray_client()
    st = client.get_job_status(job_id)

    if st == JobStatus.SUCCEEDED:
        log_text = client.get_job_logs(job_id)
        return _extract_result(log_text)
    elif st in (JobStatus.FAILED, JobStatus.STOPPED):
        log_text = client.get_job_logs(job_id)
        raise RuntimeError(f"Job {job_id} {st.value}:\n{log_text}")
    else:
        raise RuntimeError(f"Job {job_id} is still {st.value} -- not finished yet")


def logs(job_id: str) -> str:
    """Fetch the logs of a running or completed job."""
    client = get_ray_client()
    return client.get_job_logs(job_id)


def get(futures: list[Job], timeout: float | None = None) -> list[Any]:
    """Wait for submitted jobs to complete and return their results.

    Args:
        futures: List of Job objects from .remote() calls.
        timeout: Max seconds to wait. None = wait forever.
    """
    _STATUS_LABELS = {
        "PENDING": "Setting up environment",
        "RUNNING": "Running",
    }

    results: list[Any] = []
    deadline = time.time() + timeout if timeout else None

    for future in futures:
        prev_log_len = 0
        pbar = tqdm(
            bar_format="  {desc} [{elapsed}]",
            desc=f"{future.job_id}: Submitted",
        )
        try:
            while True:
                if deadline and time.time() > deadline:
                    pbar.close()
                    raise TimeoutError(f"Job {future.job_id} did not complete in time")

                job_status = future.client.get_job_status(future.job_id)

                if job_status == JobStatus.SUCCEEDED:
                    pbar.set_description_str(f"{future.job_id}: Complete")
                    pbar.close()
                    log_text = future.client.get_job_logs(future.job_id)
                    r = _extract_result(log_text)
                    results.append(r)
                    break
                elif job_status in (JobStatus.FAILED, JobStatus.STOPPED):
                    pbar.set_description_str(f"{future.job_id}: {job_status.value}")
                    pbar.close()
                    log_text = future.client.get_job_logs(future.job_id)
                    info = future.client.get_job_info(future.job_id)
                    details = ""
                    if info and info.message:
                        details += info.message + "\n"
                    if log_text:
                        details += log_text
                    raise RuntimeError(
                        f"Job {future.job_id} {job_status.value}:\n{details}"
                    )
                else:
                    label = _STATUS_LABELS.get(job_status.value, job_status.value)
                    pbar.set_description_str(f"{future.job_id}: {label}")

                    if job_status == JobStatus.RUNNING:
                        log_text = future.client.get_job_logs(future.job_id)
                        new_logs = log_text[prev_log_len:]
                        prev_log_len = len(log_text)
                        for line in new_logs.strip().splitlines():
                            if not line.startswith("__CORTEXFLOW_"):
                                tqdm.write(f"  {line}")

                    time.sleep(2)
        except BaseException:
            pbar.close()
            raise

    return results


def _extract_result(log_text: str) -> Any:
    """Extract the pickled result from job logs."""
    marker = "__CORTEXFLOW_RESULT__:"
    for line in log_text.splitlines():
        if line.startswith(marker):
            payload = line[len(marker) :]
            return pickle.loads(base64.b64decode(payload))
    return None


def get_ray_client() -> Any:
    """Return a Ray JobSubmissionClient connected to the cluster."""
    config = get_config()
    if not config.dgx_ip:
        raise RuntimeError("DGX IP not configured. Run cortexflow.init() first.")
    return JobSubmissionClient(f"http://{config.dgx_ip}:8265")


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
