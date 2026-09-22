from __future__ import annotations

from typing import Any

from cortexgrid.model_serving.placement import ModelRequirements, ray_actor_options
from cortexgrid.model_serving.serve_bundle import (
    BundleMetadata,
    bundle_fingerprint_from_url,
)


def app_name(family: str, suffix: str, run_name: str) -> str:
    return f"{family}__{suffix}__{run_name}"


def route_prefix(family: str, suffix: str, run_name: str) -> str:
    return f"/r/{family}/{suffix}/{run_name}"


def build_application_spec(
    family: str,
    suffix: str,
    run_name: str,
    meta: BundleMetadata,
    requirements: ModelRequirements,
    num_replicas: int,
    tiers: list[int],
) -> dict[str, Any]:
    """Assemble a Ray Serve application schema from pre-bundled metadata, the
    model's requirements, and the cluster's GPU size classes."""
    # working_dir carries the serve-app's own source; Ray pip-installs the
    # third-party distributions the image lacks into a per-node cached
    # virtualenv layered on the image. No pip key when there are none, so Ray
    # builds no virtualenv.
    runtime_env: dict[str, Any] = {"working_dir": meta.bundle_url}
    if meta.pip_requirements:
        runtime_env["pip"] = meta.pip_requirements
    return {
        "name": app_name(family, suffix, run_name),
        "route_prefix": route_prefix(family, suffix, run_name),
        # Ray Serve REST requires import_path to point at an Application builder
        # (callable returning a bound node) or an already-bound node. A bare
        # Deployment class is rejected, so cortexgrid.deploy_model goes through
        # a generic builder that re-imports the user's class and binds it.
        "import_path": "cortexgrid._serve_entry:build",
        "args": {
            "class_import_path": meta.class_import_path,
            "family": family,
            "suffix": suffix,
            "run_name": run_name,
            "num_replicas": num_replicas,
            "ray_actor_options": ray_actor_options(requirements, tiers),
        },
        "runtime_env": runtime_env,
    }


def bundle_fingerprint_in_spec(spec: dict[str, Any]) -> str:
    return bundle_fingerprint_from_url(spec["runtime_env"]["working_dir"])
