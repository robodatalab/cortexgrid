"""Cortexflow wrappers around Ray Serve.

Caller stays HTTP-only: deploy/undeploy/list talk to the Ray dashboard's
declarative `/api/serve/applications/` endpoint via [cortexflow.ray_util],
never `ray.init`. Code is bundled at deploy time, zipped, uploaded to MinIO
under `serve-bundles/<run_name>/<family>__<suffix>.zip`, and referenced via
`runtime_env.working_dir` so Ray workers fetch it from there.

Naming: the Ray Serve application is named "<family>__<suffix>__<run_name>".
This relies on family/suffix/run_name not containing the literal "__".

See [docs/cortexflow/model-serving.md](../docs/cortexflow/model-serving.md) for
the end-to-end design.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ray import serve
from ray.serve.deployment import Deployment as RayDeployment
from ray.serve.schema import ApplicationStatus

from cortexflow._bundle import filter_pip_freeze, stage_bundle
from cortexflow.infra import get_ray_serve_uri
from cortexflow.ray_util import (
    get_serve_details,
    put_serve_applications,
)
from cortexflow.s3_util import upload
from cortexflow.secrets import get_secret


log = logging.getLogger(__name__)


# Torch wheels for the cluster GPUs. Kept in this module rather than shared
# with cortexflow.jobs: serve apps and jobs only happen to need the same wheel
# index today; the two contexts shouldn't be coupled by a borrowed constant.
_CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu128"


@dataclass
class Deployment:
    family: str
    suffix: str
    run_name: str
    url: str
    status: str


def _app_name(family: str, suffix: str, run_name: str) -> str:
    return f"{family}__{suffix}__{run_name}"


def _route_prefix(family: str, suffix: str, run_name: str) -> str:
    return f"/r/{family}/{suffix}/{run_name}"


def model_deployment(
    num_gpus: int = 1,
    num_replicas: int = 1,
    **kw: Any,
) -> Callable[[Callable], RayDeployment]:
    """serve.deployment configured for cortexflow infra."""
    actor_options = {"num_gpus": num_gpus, **kw.pop("ray_actor_options", {})}
    return serve.deployment(
        num_replicas=num_replicas,
        ray_actor_options=actor_options,
        **kw,
    )


def _underlying_class(cls: type | RayDeployment) -> Any:
    # @cortexflow.model_deployment wraps the class as a RayDeployment; the
    # original class is reachable via func_or_class. import_path resolution on
    # the Ray worker hits the *decorated* symbol, so the bound `RayDeployment`
    # is what Ray Serve actually wants - but bundling and introspection need
    # the underlying source-defining object.
    if isinstance(cls, RayDeployment):
        return cls.func_or_class
    return cls


def _pip_requirements_for_serve(external_deps: set[str]) -> list[str]:
    """Build runtime_env.pip for a Serve app: freeze + filter + drop ray +
    inject the GH token into git URLs + prepend the torch CUDA index.
    Ray will write this list to a requirements.txt on the worker."""
    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    filtered = filter_pip_freeze(freeze, external_deps)
    token = get_secret("GH_TOKEN")
    lines: list[str] = []
    for raw in filtered.splitlines():
        name = raw.lstrip().split("==", 1)[0].split(" @", 1)[0].split("[", 1)[0]
        if name.strip().lower() == "ray" or not raw.strip():
            continue
        lines.append(
            raw.replace(
                "git+https://github.com/",
                f"git+https://x-access-token:{token}@github.com/",
            )
        )
    return [f"--extra-index-url {_CUDA_INDEX_URL}", *lines]


def _build_application_spec(
    cls: type | RayDeployment, family: str, suffix: str, run_name: str
) -> dict[str, Any]:
    """Stage code, upload to MinIO, return a Ray Serve application schema."""
    underlying = _underlying_class(cls)
    import_path = f"{underlying.__module__}:{underlying.__name__}"

    with stage_bundle(underlying) as bundle:
        pip_list = _pip_requirements_for_serve(set(bundle.external_deps))
        log.info(
            "Serve runtime_env.pip for %s/%s/%s (%d entries):\n  %s",
            family, suffix, run_name, len(pip_list), "\n  ".join(pip_list),
        )
        with tempfile.TemporaryDirectory() as tmp:
            zip_base = Path(tmp) / f"{family}__{suffix}"
            shutil.make_archive(
                str(zip_base), "zip", root_dir=str(bundle.staging_dir)
            )
            zip_path = zip_base.with_suffix(".zip")
            working_dir = upload(
                str(zip_path),
                dest_path=f"serve-bundles/{run_name}/{family}__{suffix}.zip",
            )

    return {
        "name": _app_name(family, suffix, run_name),
        "route_prefix": _route_prefix(family, suffix, run_name),
        # Ray Serve REST requires import_path to point at an Application builder
        # (callable returning a bound node) or an already-bound node. A bare
        # Deployment class is rejected, so cortexflow.deploy_model goes through
        # a generic builder that re-imports the user's class and binds it.
        "import_path": "cortexflow._serve_entry:build",
        "args": {
            "class_import_path": import_path,
            "family": family,
            "suffix": suffix,
            "run_name": run_name,
        },
        "runtime_env": {"working_dir": working_dir, "pip": pip_list},
    }


def _current_application_specs() -> list[dict[str, Any]]:
    """Reconstruct the most recently PUT applications list from GET output.

    Ray stores the originally-deployed `ServeApplicationSchema` for each app
    under `applications[<name>].deployed_app_config`, which is what we need to
    PUT-round-trip. Apps without a `deployed_app_config` (e.g. created via
    `serve.run` in-cluster) are skipped: we can't faithfully reproduce them
    from the read-only view.
    """
    details = get_serve_details()
    specs: list[dict[str, Any]] = []
    for app in details.get("applications", {}).values():
        cfg = app.get("deployed_app_config")
        if cfg is not None:
            specs.append(cfg)
    return specs


def _wait_for_application_running(
    name: str, timeout_s: float = 300.0, interval_s: float = 2.0
) -> None:
    """Poll the Serve controller until the named application is RUNNING.

    Raises immediately on DEPLOY_FAILED with the controller's message. Other
    non-RUNNING statuses (NOT_STARTED, DEPLOYING, UNHEALTHY) are treated as
    transient until the timeout fires.
    """
    deadline = time.monotonic() + timeout_s
    last_status: str = "(missing)"
    last_message: str = ""
    while time.monotonic() < deadline:
        app = get_serve_details().get("applications", {}).get(name)
        if app is not None:
            last_status = str(app.get("status", "(missing)"))
            last_message = str(app.get("message", ""))
            if last_status == ApplicationStatus.RUNNING.value:
                return
            if last_status == ApplicationStatus.DEPLOY_FAILED.value:
                raise RuntimeError(
                    f"Serve app {name!r} DEPLOY_FAILED: {last_message}"
                )
        time.sleep(interval_s)
    raise TimeoutError(
        f"Serve app {name!r} did not reach RUNNING within {timeout_s}s "
        f"(last status={last_status!r}, message={last_message!r})"
    )


def deploy_model(
    cls: type | RayDeployment,
    family: str,
    suffix: str,
    run_name: str,
    wait: bool = False,
) -> Deployment:
    """Schedule a Ray Serve app bound to (family, suffix, run_name).

    `cls` must be a class decorated with @model_deployment (or @serve.deployment).
    The union accepts both views of the decorated value: pyright tracks the
    transformation and sees a Deployment, mypy keeps the original `type`.

    With `wait=True`, blocks until the Serve controller reports the app
    RUNNING (5 min cap). DEPLOY_FAILED raises; timing out raises.
    """
    spec = _build_application_spec(cls, family, suffix, run_name)
    existing = [a for a in _current_application_specs() if a["name"] != spec["name"]]
    put_serve_applications([*existing, spec])
    if wait:
        _wait_for_application_running(spec["name"])
    return Deployment(
        family=family,
        suffix=suffix,
        run_name=run_name,
        url=f"{get_ray_serve_uri()}{_route_prefix(family, suffix, run_name)}",
        status="running" if wait else "deploying",
    )


def undeploy_model(family: str, suffix: str, run_name: str) -> None:
    """Tear down the Ray Serve app for this model."""
    name = _app_name(family, suffix, run_name)
    remaining = [a for a in _current_application_specs() if a["name"] != name]
    put_serve_applications(remaining)


def list_deployed_models() -> list[Deployment]:
    """Return Deployment records for every Ray Serve app whose name matches our scheme."""
    base = get_ray_serve_uri()
    details = get_serve_details()
    result: list[Deployment] = []
    for app_name, app in details.get("applications", {}).items():
        parts = app_name.split("__")
        if len(parts) != 3:
            continue
        family, suffix, run_name = parts
        result.append(
            Deployment(
                family=family,
                suffix=suffix,
                run_name=run_name,
                url=f"{base}{_route_prefix(family, suffix, run_name)}",
                status=str(app.get("status", "unknown")),
            )
        )
    return result
