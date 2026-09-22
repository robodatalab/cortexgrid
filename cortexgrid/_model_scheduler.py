from __future__ import annotations

import logging
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

import ray
from ray.actor import ActorProxy
from ray.serve.config import AutoscalingContext
from ray.util.state import list_actors


log = logging.getLogger(__name__)


_RayResources = dict[str, float]


_SCHEDULER_ACTOR_NAME = "cortexgrid-model-scheduler"
_SCHEDULER_ACTOR_NAMESPACE = "cortexgrid"

_PAUSE_DECISION_INTERVAL_S = 1.0
_FORGET_MODELS_SILENT_FOR_S = 30.0

_REQUEST_METRICS_PUSH_INTERVAL_S = 0.5
_REQUEST_METRICS_AVERAGING_WINDOW_S = 1.0

_STATE_API_RESULT_LIMIT = 10_000
_RESOURCE_FLOAT_TOLERANCE = 1e-6


def model_autoscaling_config(
    max_replicas: int, ray_actor_options: dict[str, Any]
) -> dict[str, Any]:
    return {
        "min_replicas": 0,
        "initial_replicas": max_replicas,
        "max_replicas": max_replicas,
        "upscale_delay_s": 0,
        "downscale_delay_s": 0,
        "downscale_to_zero_delay_s": 0,
        "metrics_interval_s": _REQUEST_METRICS_PUSH_INTERVAL_S,
        "look_back_period_s": _REQUEST_METRICS_AVERAGING_WINDOW_S,
        "policy": {
            "policy_function": (
                f"{ModelAutoscalingPolicy.__module__}:"
                f"{ModelAutoscalingPolicy.__qualname__}"
            ),
            "policy_kwargs": {
                "replica_resources": _resources_requested_by_replica(
                    ray_actor_options
                )
            },
        },
    }


def _resources_requested_by_replica(
    ray_actor_options: dict[str, Any],
) -> _RayResources:
    resources = {
        name: float(amount)
        for name, amount in ray_actor_options.get("resources", {}).items()
    }
    if ray_actor_options.get("num_gpus"):
        resources["GPU"] = float(ray_actor_options["num_gpus"])
    if ray_actor_options.get("memory"):
        resources["memory"] = float(ray_actor_options["memory"])
    return resources


class ModelAutoscalingPolicy:
    def __init__(self, replica_resources: _RayResources) -> None:
        self._replica_resources = replica_resources
        self._scheduler: ActorProxy[_ModelScheduler] | None = None
        self._pending_pause_answer: Future[bool] | None = None
        self._pause_requested_by_scheduler = False

    def __call__(self, context: AutoscalingContext) -> tuple[int, dict[str, Any]]:
        has_requests = context.total_num_requests > 0
        waiting_for_replica = (
            has_requests or context.target_num_replicas > 0
        ) and not context.running_replicas
        if not self._awaiting_pause_answer():
            self._pause_requested_by_scheduler = self._collect_pause_answer()
            self._send_activity_report(
                context.deployment_id.to_replica_actor_class_name(),
                has_requests,
                waiting_for_replica,
            )
        return self._replica_count(context, has_requests), context.policy_state

    def _replica_count(self, context: AutoscalingContext, has_requests: bool) -> int:
        if has_requests:
            return context.capacity_adjusted_max_replicas
        if self._pause_requested_by_scheduler:
            return 0
        return context.target_num_replicas

    def _awaiting_pause_answer(self) -> bool:
        return (
            self._pending_pause_answer is not None
            and not self._pending_pause_answer.done()
        )

    def _collect_pause_answer(self) -> bool:
        if self._pending_pause_answer is None:
            return False
        try:
            return self._pending_pause_answer.result()
        except Exception:
            log.exception("Model scheduler unreachable")
            self._scheduler = None
            return False

    def _send_activity_report(
        self, replica_class_name: str, has_requests: bool, waiting_for_replica: bool
    ) -> None:
        if self._scheduler is None:
            self._scheduler = _get_or_create_scheduler_actor()
        self._pending_pause_answer = (
            self._scheduler.report_activity_and_check_pause.remote(
                replica_class_name,
                self._replica_resources,
                has_requests,
                waiting_for_replica,
            ).future()
        )


def _get_or_create_scheduler_actor() -> ActorProxy[_ModelScheduler]:
    return (
        ray.remote(_ModelScheduler)
        .options(
            name=_SCHEDULER_ACTOR_NAME,
            namespace=_SCHEDULER_ACTOR_NAMESPACE,
            get_if_exists=True,
            lifetime="detached",
            num_cpus=0,
            max_restarts=-1,
        )
        .remote()
    )


@dataclass
class _ScheduledModel:
    replica_resources: _RayResources
    has_requests: bool = False
    waiting_for_replica: bool = False
    waiting_since: float = 0.0
    last_request_at: float = 0.0
    last_report_at: float = 0.0


@dataclass
class _NodeOccupancy:
    free_resources: _RayResources
    resources_held_by_model: dict[str, _RayResources] = field(default_factory=dict)


class _ModelScheduler:
    def __init__(self) -> None:
        self._models: dict[str, _ScheduledModel] = {}
        self._models_to_pause: set[str] = set()
        self._last_pause_decision_at = float("-inf")

    @ray.method
    def report_activity_and_check_pause(
        self,
        replica_class_name: str,
        replica_resources: _RayResources,
        has_requests: bool,
        waiting_for_replica: bool,
    ) -> bool:
        now = time.monotonic()
        self._record_activity(
            replica_class_name, replica_resources, has_requests, waiting_for_replica, now
        )
        if now - self._last_pause_decision_at >= _PAUSE_DECISION_INTERVAL_S:
            self._last_pause_decision_at = now
            self._forget_models_silent_since(now - _FORGET_MODELS_SILENT_FOR_S)
            self._models_to_pause = self._select_models_to_pause()
        return replica_class_name in self._models_to_pause

    def _record_activity(
        self,
        replica_class_name: str,
        replica_resources: _RayResources,
        has_requests: bool,
        waiting_for_replica: bool,
        now: float,
    ) -> None:
        model = self._models.setdefault(
            replica_class_name, _ScheduledModel(replica_resources)
        )
        model.replica_resources = replica_resources
        if waiting_for_replica and not model.waiting_for_replica:
            model.waiting_since = now
        if has_requests:
            model.last_request_at = now
        model.has_requests = has_requests
        model.waiting_for_replica = waiting_for_replica
        model.last_report_at = now

    def _forget_models_silent_since(self, cutoff: float) -> None:
        self._models = {
            name: model
            for name, model in self._models.items()
            if model.last_report_at >= cutoff
        }

    def _select_models_to_pause(self) -> set[str]:
        if not any(model.waiting_for_replica for model in self._models.values()):
            return set()
        nodes = _read_node_occupancy(set(self._models))
        models_with_placed_replicas = {
            name for node in nodes for name in node.resources_held_by_model
        }
        models_waiting_for_room = sorted(
            (
                name
                for name, model in self._models.items()
                if model.waiting_for_replica
                and name not in models_with_placed_replicas
            ),
            key=lambda name: self._models[name].waiting_since,
        )
        models_to_pause: set[str] = set()
        for name in models_waiting_for_room:
            models_to_pause |= self._fewest_idle_models_to_pause_for(
                self._models[name].replica_resources, nodes, models_to_pause
            )
        return models_to_pause

    def _fewest_idle_models_to_pause_for(
        self,
        required_resources: _RayResources,
        nodes: list[_NodeOccupancy],
        already_pausing: set[str],
    ) -> set[str]:
        fewest_models_to_pause: list[str] | None = None
        for node in nodes:
            if _has_room_for(required_resources, node.free_resources):
                return set()
            models_to_pause = self._least_recently_used_idle_models_freeing(
                required_resources, node, already_pausing
            )
            if models_to_pause is not None and (
                fewest_models_to_pause is None
                or len(models_to_pause) < len(fewest_models_to_pause)
            ):
                fewest_models_to_pause = models_to_pause
        return set(fewest_models_to_pause or ())

    def _least_recently_used_idle_models_freeing(
        self,
        required_resources: _RayResources,
        node: _NodeOccupancy,
        already_pausing: set[str],
    ) -> list[str] | None:
        idle_models_least_recently_used_first = sorted(
            (
                name
                for name in node.resources_held_by_model
                if name not in already_pausing and not self._models[name].has_requests
            ),
            key=lambda name: self._models[name].last_request_at,
        )
        resources_free_after_pause = dict(node.free_resources)
        models_to_pause: list[str] = []
        for name in idle_models_least_recently_used_first:
            models_to_pause.append(name)
            _add_resources(
                resources_free_after_pause, node.resources_held_by_model[name]
            )
            if _has_room_for(required_resources, resources_free_after_pause):
                return models_to_pause
        return None


def _read_node_occupancy(
    scheduled_replica_class_names: set[str],
) -> list[_NodeOccupancy]:
    occupancy_by_node_id = {
        node["NodeID"]: _NodeOccupancy(free_resources=dict(node["Resources"]))
        for node in ray.nodes()
        if node["Alive"]
    }
    for actor in list_actors(
        filters=[("state", "=", "ALIVE")],
        detail=True,
        limit=_STATE_API_RESULT_LIMIT,
        raise_on_missing_output=False,
    ):
        actor_fields = vars(actor)
        occupancy = occupancy_by_node_id.get(actor_fields["node_id"])
        if occupancy is None:
            continue
        reserved_resources = actor_fields["required_resources"] or {}
        _subtract_resources(occupancy.free_resources, reserved_resources)
        replica_class_name = actor_fields["class_name"]
        if replica_class_name in scheduled_replica_class_names:
            _add_resources(
                occupancy.resources_held_by_model.setdefault(replica_class_name, {}),
                reserved_resources,
            )
    return list(occupancy_by_node_id.values())


def _add_resources(target: _RayResources, amounts: _RayResources) -> None:
    for name, amount in amounts.items():
        target[name] = target.get(name, 0.0) + amount


def _subtract_resources(target: _RayResources, amounts: _RayResources) -> None:
    for name, amount in amounts.items():
        target[name] = target.get(name, 0.0) - amount


def _has_room_for(required: _RayResources, available: _RayResources) -> bool:
    return all(
        available.get(name, 0.0) + _RESOURCE_FLOAT_TOLERANCE >= amount
        for name, amount in required.items()
    )
