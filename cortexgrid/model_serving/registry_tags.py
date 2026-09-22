from __future__ import annotations

import json

from cortexgrid import state
from cortexgrid.model_serving.placement import ModelRequirements
from cortexgrid.model_serving.serve_bundle import BundleMetadata


# Registry tag keys for the bundle metadata `save_model` writes and
# `deploy_model` reads back.
_CLASS_IMPORT_PATH_TAG = "class_import_path"
_BUNDLE_URL_TAG = "serve_bundle_url"
_PIP_REQUIREMENTS_TAG = "serve_pip_requirements"
_BUNDLE_FINGERPRINT_TAG = "serve_bundle_fingerprint"


def metadata_to_tags(meta: BundleMetadata) -> dict[str, str]:
    """Serialise BundleMetadata to registry tags. The inverse of
    `metadata_from_tags`; lives here next to the consumer so the tag schema
    stays in one place."""
    return {
        _CLASS_IMPORT_PATH_TAG: meta.class_import_path,
        _BUNDLE_URL_TAG: meta.bundle_url,
        _PIP_REQUIREMENTS_TAG: json.dumps(meta.pip_requirements),
        _BUNDLE_FINGERPRINT_TAG: meta.fingerprint,
    }


def metadata_from_tags(tags: dict[str, str]) -> BundleMetadata:
    """Deserialise BundleMetadata from a registry entry's tags. Raises
    KeyError for a missing bundle URL or class import path."""
    return BundleMetadata(
        bundle_url=tags[_BUNDLE_URL_TAG],
        class_import_path=tags[_CLASS_IMPORT_PATH_TAG],
        # Absent on models saved before dependencies were pip-installed.
        pip_requirements=json.loads(tags.get(_PIP_REQUIREMENTS_TAG, "[]")),
        # Absent on models saved before bundles were fingerprinted.
        fingerprint=tags.get(_BUNDLE_FINGERPRINT_TAG, ""),
    )


# Registry tag keys for the ModelRequirements.
_NUM_GPUS_TAG = "num_gpus"
_RAM_GB_TAG = "ram_gb"
_VRAM_GB_TAG = "vram_gb"


def has_requirement_tags(tags: dict[str, str]) -> bool:
    """Whether requirements were ever stored on the registry entry."""
    return any(key in tags for key in (_NUM_GPUS_TAG, _RAM_GB_TAG, _VRAM_GB_TAG))


def requirements_to_tags(requirements: ModelRequirements) -> dict[str, str]:
    """Serialise ModelRequirements to registry tags. The inverse of
    `requirements_from_tags`."""
    return {
        _NUM_GPUS_TAG: str(requirements.num_gpus),
        _RAM_GB_TAG: str(requirements.ram_gb),
        _VRAM_GB_TAG: str(requirements.vram_gb),
    }


def requirements_from_tags(tags: dict[str, str]) -> ModelRequirements:
    """Deserialise ModelRequirements from a registry entry's tags; a
    missing tag reads as no requirement."""
    return ModelRequirements(
        # float, not int: models saved before GPUs could be shared stored a
        # whole number, which parses as one.
        num_gpus=float(tags.get(_NUM_GPUS_TAG, "0")),
        ram_gb=float(tags.get(_RAM_GB_TAG, "0")),
        vram_gb=float(tags.get(_VRAM_GB_TAG, "0")),
    )


def load_deploy_metadata(
    family: str, suffix: str, run_name: str
) -> tuple[BundleMetadata, ModelRequirements]:
    """Read the bundle metadata and requirements `save_model` persisted on the
    registry entry."""
    record = state.get("models", family, suffix, run_name)
    if record is None:
        raise ValueError(
            f"No saved model for {family}/{suffix}/{run_name}; cannot deploy."
        )
    tags = record["tags"]
    try:
        return metadata_from_tags(tags), requirements_from_tags(tags)
    except KeyError as exc:
        raise ValueError(
            f"Saved model {family}/{suffix}/{run_name} is missing the deployment "
            f"bundle tag {exc.args[0]!r}; re-save with `cortexgrid.save_model`."
        ) from exc
