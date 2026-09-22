"""Models stream.

Single pinned topic: every entry in cortexgrid's model registry,
surfaced as SavedModel-shaped payloads. Items are keyed by
`<family>/<suffix>/<run_name>` so the UI can address each version.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cortexgrid.model_serving import ModelRequirements
from cortexgrid.model_storage import list_models

from cortexgrid_ui.backend.streams.config import EXPERIMENTS_STREAM_POLL_INTERVAL_SEC
from cortexgrid_ui.backend.utils.keyed_stream import KeyedCache, Refresher


ModelId = str
META_TOPIC: None = None


@dataclass
class Model:
    id: ModelId
    family: str
    suffix: str
    run_name: str
    created_at: str
    data_blob_path: str
    size_bytes: int
    # Registry lifecycle phase: "uploading" while the weights stream to storage,
    # "ready" once registered, "upload_failed" on error. Lets the dashboard show
    # a model that is still uploading and not yet deployable.
    phase: str
    # Hardware one replica needs; the dashboard shows it and can edit it.
    requirements: ModelRequirements
    bundle_fingerprint: str
    # Free-form settings the serve-app reads at construction; the dashboard
    # shows them and can edit them.
    config: dict[str, str] = field(default_factory=dict)


def model_id(family: str, suffix: str, run_name: str) -> ModelId:
    return f"{family}/{suffix}/{run_name}"


def poll_models(_: None) -> dict[ModelId, Model]:
    out: dict[ModelId, Model] = {}
    for m in list_models():
        mid = model_id(m.family, m.suffix, m.run_name)
        out[mid] = Model(
            id=mid,
            family=m.family,
            suffix=m.suffix,
            run_name=m.run_name,
            created_at=m.created_at,
            data_blob_path=m.data_blob_path,
            size_bytes=m.size_bytes,
            phase=m.phase,
            requirements=m.requirements,
            bundle_fingerprint=m.bundle_fingerprint,
            config=m.config,
        )
    return out


models_cache: KeyedCache[None, ModelId, Model] = KeyedCache()

models_refresher: Refresher[None, ModelId, Model] = Refresher(
    name="models_stream",
    cache=models_cache,
    poll_fn=poll_models,
    poll_interval_sec=EXPERIMENTS_STREAM_POLL_INTERVAL_SEC,
)
