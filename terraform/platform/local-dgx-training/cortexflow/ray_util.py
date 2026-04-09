"""Ray wrappers.

cortexflow.remote — wraps a function for execution on the DGX via the Ray Jobs API
cortexflow.get    — waits for submitted jobs and returns results

Uses the Ray Jobs API (HTTP to :8265). The Mac never joins the cluster.
"""

from __future__ import annotations

import base64
import pickle
import textwrap
import time
from typing import Any

from cortexflow.config import get_config
from cortexflow.project import build_runtime_env


class _JobFuture:
    """Handle to a submitted Ray job."""

    def __init__(self, client: Any, job_id: str) -> None:
        self.client = client
        self.job_id = job_id


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

    def remote(self, *args: Any, **kwargs: Any) -> _JobFuture:
        """Submit this function to the Ray cluster. Returns a future."""
        from ray.job_submission import JobSubmissionClient

        config = get_config()
        client = JobSubmissionClient(f"http://{config.dgx_ip}:8265")

        # Serialize function and arguments
        payload = base64.b64encode(pickle.dumps({
            "fn": self._fn,
            "args": args,
            "kwargs": kwargs,
            "num_gpus": self._num_gpus,
            "max_retries": self._max_retries,
        })).decode()

        # Driver script that runs inside the Ray cluster
        driver = textwrap.dedent("""\
            import base64, pickle, sys
            import ray

            payload = pickle.loads(base64.b64decode(sys.argv[1]))
            fn = payload["fn"]
            args = payload["args"]
            kwargs = payload["kwargs"]

            ray.init()

            remote_fn = ray.remote(
                num_gpus=payload["num_gpus"],
                max_retries=payload["max_retries"],
            )(fn)
            result = ray.get(remote_fn.remote(*args, **kwargs))

            # Write result for retrieval
            import json
            result_bytes = base64.b64encode(pickle.dumps(result)).decode()
            print(f"__CORTEXFLOW_RESULT__:{result_bytes}")
        """)

        entrypoint = f"python -c {_shell_quote(driver)} {payload}"

        job_id = client.submit_job(
            entrypoint=entrypoint,
            runtime_env=self._runtime_env,
        )

        return _JobFuture(client, job_id)


def remote(
    *args: Any,
    num_gpus: int = 0,
    num_cpus: int = 1,
    max_retries: int = 0,
    retry_exceptions: bool = False,
    **kwargs: Any,
) -> Any:
    """Wrap a function for remote execution on the DGX cluster.

    Reads the project's pyproject.toml to build the runtime_env automatically.
    Submits via the Ray Jobs API — no local Ray init needed.

    Usage:
        train_fn = cortexflow.remote(num_gpus=1, max_retries=3)(my_function)
        future = train_fn.remote(arg1, arg2)
        result = cortexflow.get([future])
    """
    runtime_env = build_runtime_env()

    config = get_config()
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


def get(futures: list[_JobFuture], timeout: float | None = None) -> list[Any]:
    """Wait for submitted jobs to complete and return their results.

    Args:
        futures: List of _JobFuture objects from .remote() calls.
        timeout: Max seconds to wait. None = wait forever.
    """
    from ray.job_submission import JobStatus

    results: list[Any] = []
    deadline = time.time() + timeout if timeout else None

    for future in futures:
        while True:
            if deadline and time.time() > deadline:
                raise TimeoutError(f"Job {future.job_id} did not complete in time")

            status = future.client.get_job_status(future.job_id)

            if status == JobStatus.SUCCEEDED:
                logs = future.client.get_job_logs(future.job_id)
                result = _extract_result(logs)
                results.append(result)
                break
            elif status in (JobStatus.FAILED, JobStatus.STOPPED):
                logs = future.client.get_job_logs(future.job_id)
                info = future.client.get_job_info(future.job_id)
                details = ""
                if info and info.message:
                    details += info.message + "\n"
                if logs:
                    details += logs
                raise RuntimeError(
                    f"Job {future.job_id} {status.value}:\n{details}"
                )
            else:
                time.sleep(2)

    return results


def _extract_result(logs: str) -> Any:
    """Extract the pickled result from job logs."""
    marker = "__CORTEXFLOW_RESULT__:"
    for line in logs.splitlines():
        if line.startswith(marker):
            payload = line[len(marker):]
            return pickle.loads(base64.b64decode(payload))
    return None


def get_ray_client() -> Any:
    """Return a Ray JobSubmissionClient connected to the cluster."""
    from ray.job_submission import JobSubmissionClient

    config = get_config()
    if not config.dgx_ip:
        raise RuntimeError("DGX IP not configured. Run cortexflow.init() first.")
    return JobSubmissionClient(f"http://{config.dgx_ip}:8265")


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
