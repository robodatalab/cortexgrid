"""Cortexgrid wrappers around Ray Serve.

Caller stays HTTP-only: deploy/undeploy/list talk to the Ray dashboard's
declarative `/api/serve/applications/` endpoint via [cortexgrid.ray_util],
never `ray.init`. The deployment class is bundled at `save_model` time, zipped,
uploaded to MinIO under
`serve-bundles/<run_name>/<family>__<suffix>/<fingerprint>.zip`, and
referenced via `runtime_env.working_dir` so Ray workers fetch it from there.
The bundle URL, class import path, pip list, and fingerprint are persisted as
tags on the model's registry entry so `deploy_model` can find them later
without the caller holding the class object. So are the model's
`ModelRequirements`, which `deploy_model` turns into the replica's Ray resource
requests.

Every deployment `deploy_model` puts on Ray Serve gets a record with the jobs
control plane, keyed by its `DeploymentKey` - the model's (family, suffix,
run_name) plus a fingerprint of the config it was deployed with: that config,
the spec it PUT, plus the phase, message and replica
placements the control plane last observed (`observe_deployments`, run on
every poll cycle). Listings and status reads come from those records; waits
ask the Serve controller directly.

Naming: the Ray Serve application is named "<family>__<suffix>__<run_name>",
followed by "__<config_fingerprint>" for a deployment given a config.
This relies on family/suffix/run_name not containing the literal "__".

See [docs/cortexgrid/model-serving.md](../../docs/cortexgrid/model-serving.md)
for the end-to-end design.
"""

from cortexgrid.model_serving.application_spec import app_name
from cortexgrid.model_serving.deployment_key import (
    DeploymentConfig,
    DeploymentKey,
    deployment_key,
)
from cortexgrid.model_serving.lifecycle import (
    ModelDeployFailed,
    ModelNotDeployed,
    deploy_model,
    redeploy_model,
    undeploy_model,
    wait_for_model_serving,
)
from cortexgrid.model_serving.placement import ModelRequirements, vram_tiers
from cortexgrid.model_serving.registry_tags import (
    bundle_fingerprint_from_tags,
    has_requirement_tags,
    metadata_from_tags,
    metadata_to_tags,
    requirements_from_tags,
    requirements_to_tags,
)
from cortexgrid.model_serving.serve_bundle import (
    BundleMetadata,
    ServeBundle,
    build_bundle,
    bundle_class,
    bundle_fingerprint_from_url,
    upload_bundle,
)
from cortexgrid.model_serving.status import (
    Deployment,
    ReplicaPlacement,
    ServingMessage,
    ServingStatus,
    deployment_config,
    list_deployed_models,
    model_replica_placements,
    model_serving_messages,
    model_serving_status,
    observe_deployments,
)
