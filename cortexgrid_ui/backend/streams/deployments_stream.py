"""Deployments stream.

Single pinned topic: every model `deploy_model` put on Ray Serve, from its
deployment record, surfaced as the existing
`cortexgrid.model_serving.Deployment` dataclass. Items are
keyed by `<family>/<suffix>/<run_name>` so the UI can join them against
`models_stream` by the same id.
"""

from __future__ import annotations

from cortexgrid.model_serving import Deployment, list_deployed_models

from cortexgrid_ui.backend.streams.config import EXPERIMENTS_STREAM_POLL_INTERVAL_SEC
from cortexgrid_ui.backend.utils.keyed_stream import KeyedCache, Refresher


DeploymentId = str
META_TOPIC: None = None


def deployment_id(family: str, suffix: str, run_name: str) -> DeploymentId:
    return f"{family}/{suffix}/{run_name}"


def poll_deployments(_: None) -> dict[DeploymentId, Deployment]:
    return {
        deployment_id(d.family, d.suffix, d.run_name): d
        for d in list_deployed_models()
    }


deployments_cache: KeyedCache[None, DeploymentId, Deployment] = KeyedCache()

deployments_refresher: Refresher[None, DeploymentId, Deployment] = Refresher(
    name="deployments_stream",
    cache=deployments_cache,
    poll_fn=poll_deployments,
    poll_interval_sec=EXPERIMENTS_STREAM_POLL_INTERVAL_SEC,
)
