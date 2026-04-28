"""Infrastructure status — queries the kubernetes API for pod health across all namespaces."""

from __future__ import annotations

from kubernetes import client, config  # type: ignore
from kubernetes.client.rest import ApiException  # type: ignore
from pydantic import BaseModel


class PodStatus(BaseModel):
    name: str
    namespace: str
    kind: str = "pod"
    node: str | None = None
    state: str
    health: str
    healthy: bool
    logs: str | None = None


class InfraStatus(BaseModel):
    overall: bool
    pods: list[PodStatus]


def _load_kube_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _classify(pod, unreachable_nodes: dict[str, str] | None = None) -> tuple[bool, str]:
    if unreachable_nodes and pod.spec.node_name in unreachable_nodes:
        return False, f"node-unreachable: {unreachable_nodes[pod.spec.node_name]}"
    phase = pod.status.phase or "Unknown"
    statuses = pod.status.container_statuses or []
    if phase == "Succeeded":
        return True, "succeeded"
    if phase == "Running" and statuses and all(cs.ready for cs in statuses):
        return True, "ready"
    waiting = next(
        (cs.state.waiting for cs in statuses if cs.state and cs.state.waiting), None
    )
    return False, (waiting.reason if waiting and waiting.reason else "not-ready")


def _fetch_logs(v1: client.CoreV1Api, pod) -> str | None:
    try:
        return v1.read_namespaced_pod_log(
            name=pod.metadata.name,
            namespace=pod.metadata.namespace,
            tail_lines=50,
        )
    except ApiException:
        return None


def _pod_to_status(
    pod, v1: client.CoreV1Api, unreachable_nodes: dict[str, str] | None = None
) -> PodStatus:
    healthy, health = _classify(pod, unreachable_nodes)
    return PodStatus(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        node=pod.spec.node_name,
        state=pod.status.phase or "Unknown",
        health=health,
        healthy=healthy,
        logs=None if healthy else _fetch_logs(v1, pod),
    )


def _collect_unreachable_nodes(v1: client.CoreV1Api) -> dict[str, str]:
    unreachable: dict[str, str] = {}
    for node in v1.list_node().items:
        ready = next(
            (c for c in (node.status.conditions or []) if c.type == "Ready"),
            None,
        )
        if ready is None or ready.status != "True":
            unreachable[node.metadata.name] = (
                (ready.reason or ready.status) if ready else "Unknown"
            )
    return unreachable


def get_infra_status() -> InfraStatus:
    _load_kube_config()
    v1 = client.CoreV1Api()
    unreachable_nodes = _collect_unreachable_nodes(v1)
    pods = v1.list_pod_for_all_namespaces(watch=False).items
    statuses = sorted(
        (_pod_to_status(p, v1, unreachable_nodes) for p in pods),
        key=lambda s: (s.namespace, s.name),
    )
    overall = all(s.healthy for s in statuses)
    return InfraStatus(overall=overall, pods=statuses)
