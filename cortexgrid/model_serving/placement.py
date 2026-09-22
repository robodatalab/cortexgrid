from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cortexgrid.ray_util import get_ray_nodes


@dataclass
class ModelRequirements:
    """Hardware one replica of a model needs to be served, in GiB. Persisted as
    tags on the registry entry next to the bundle metadata, so it is read without
    touching the weights or importing the serve-app class.

    Zero means no requirement: a model with no requirements is served on any
    node, CPU-only included. Models saved before requirements existed carry
    no tags and read as the defaults."""

    # Fractional, so several models can share one card: 0.25 puts four
    # replicas on a GPU. Ray does not isolate them - they all see the same
    # device - so what keeps them from overcommitting its memory is `vram_gb`,
    # which is reserved from the card's `vram_mib` and is the real limit.
    num_gpus: float = 0.0
    ram_gb: float = 0.0
    # GPU memory across the replica's num_gpus GPUs, so it needs a GPU share.
    vram_gb: float = 0.0

    def __post_init__(self) -> None:
        if self.num_gpus < 0 or self.ram_gb < 0 or self.vram_gb < 0:
            raise ValueError(f"Model requirements cannot be negative: {self}")
        if self.vram_gb > 0 and self.num_gpus == 0:
            raise ValueError(
                f"vram_gb={self.vram_gb} needs a GPU; set num_gpus > 0"
            )


# Custom Ray resource each GPU worker advertises: the MiB of memory its GPUs
# have (see the ray-worker DaemonSet). A replica requests its vram_gb of it in
# MiB, so Ray places it only on a node with that much VRAM left. MiB because
# nvidia-smi reports MiB and a GPU's memory is not a whole number of GiB.
_VRAM_RESOURCE = "vram_mib"

_MIB_PER_GIB = 1024

_GIB = 1024**3


# Node label each GPU worker sets to the same MiB it advertises as the
# `_VRAM_RESOURCE` (see the ray-worker DaemonSet). The resource reserves VRAM;
# the label names the size class of the node's GPUs, which is what lets a
# replica ask for the smallest card that fits. Nodes with no GPU carry neither.
_VRAM_LABEL = "vram_mib"


def vram_tiers() -> list[int]:
    """The distinct GPU sizes in the cluster, in MiB, smallest first.

    One entry per size class, not per node: two 12 GiB workers are one tier.
    Nodes that are not ALIVE, and nodes with no `vram_mib` label (CPU workers,
    the head), contribute none - so a cluster with no GPUs reports no tiers.
    """
    tiers = set()
    for node in get_ray_nodes():
        if node.get("state") != "ALIVE":
            continue
        label = (node.get("labels") or {}).get(_VRAM_LABEL)
        # A worker that could not size its GPUs never starts, so a malformed
        # label means someone set it by hand; skip it rather than fail every
        # deploy in the cluster.
        if label is not None and label.isdigit():
            tiers.add(int(label))
    return sorted(tiers)


def _placement_preferences(needed_mib: int, tiers: list[int]) -> list[dict[str, Any]]:
    """The size classes a replica needing `needed_mib` should be offered, best
    first: every tier large enough, smallest first, then a catch-all for any
    tier that is not too small.

    The catch-all is what keeps this from going stale. It excludes the sizes
    known to be too small rather than naming the ones that fit, so a larger
    GPU joining the cluster after this deploy is still placeable without a
    redeploy. It is dropped when no known tier is too small, since there is
    then nothing left for it to say.

    Excluding rather than naming also matches a node carrying no `vram_mib`
    label at all. That is harmless: a model reaching here has VRAM to reserve,
    so it also requests `num_gpus` and `_VRAM_RESOURCE`, neither of which a
    CPU-only node has. The resource request, not the selector, is what keeps
    a GPU model off a CPU node.
    """
    # Sorted here rather than trusted from the caller: the whole contract is
    # "smallest first", and it must not rest on how the tiers arrived.
    ordered = sorted(tiers)
    fits = [tier for tier in ordered if tier >= needed_mib]
    too_small = [str(tier) for tier in ordered if tier < needed_mib]
    preferences = [{_VRAM_LABEL: str(tier)} for tier in fits]
    if too_small:
        preferences.append({_VRAM_LABEL: f"!in({', '.join(too_small)})"})
    return preferences


def _placement_options(
    requirements: ModelRequirements, tiers: list[int]
) -> dict[str, Any]:
    """Ask Ray for the smallest GPU that fits, falling back to larger ones.

    `label_selector` names the smallest size class the model fits on, and
    `fallback_strategy` the larger ones in order, so Ray reaches for a bigger
    card only once every smaller one is out of VRAM. That a fallback fires on
    exhaustion, and not merely on a size class being absent, is what makes
    this "smallest that is free" rather than "smallest that exists"; checked
    against Ray 2.58, which is the floor this package pins for it.

    These only order the candidates. The reservation is still the `vram_mib`
    resource, so two replicas can no more share a card's memory than before,
    and a selector matching nothing leaves the replica pending exactly as an
    unsatisfiable resource request does.

    A model with no VRAM requirement gets no selector at all, so it stays
    placeable on a CPU-only node - and so does every model when the cluster
    reports no GPU sizes, which is the pre-label behaviour.
    """
    if requirements.vram_gb <= 0 or not tiers:
        return {}
    preferences = _placement_preferences(
        round(requirements.vram_gb * _MIB_PER_GIB), tiers
    )
    first, *rest = preferences
    options: dict[str, Any] = {"label_selector": first}
    if rest:
        options["fallback_strategy"] = [{"label_selector": r} for r in rest]
    return options


def ray_actor_options(
    requirements: ModelRequirements, tiers: list[int]
) -> dict[str, Any]:
    """Translate ModelRequirements into a replica's Ray actor resource requests.
    Ray places the replica only on a node with that much free and reserves it
    there; a zero requirement requests nothing. `tiers` are the cluster's GPU
    size classes, which decide which node Ray prefers among those that fit."""
    options: dict[str, Any] = {"num_gpus": requirements.num_gpus}
    if requirements.ram_gb > 0:
        options["memory"] = int(requirements.ram_gb * _GIB)
    if requirements.vram_gb > 0:
        options["resources"] = {
            _VRAM_RESOURCE: round(requirements.vram_gb * _MIB_PER_GIB)
        }
    options.update(_placement_options(requirements, tiers))
    return options
