"""Infrastructure status — queries the kubernetes API for pod health across all namespaces."""

from __future__ import annotations

from kubernetes import client, config  # type: ignore
from kubernetes.client.rest import ApiException  # type: ignore
from pydantic import BaseModel


class PodStatus(BaseModel):
    name: str
    namespace: str
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


def _classify(pod) -> tuple[bool, str]:
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


def _pod_to_status(pod, v1: client.CoreV1Api) -> PodStatus:
    healthy, health = _classify(pod)
    return PodStatus(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        state=pod.status.phase or "Unknown",
        health=health,
        healthy=healthy,
        logs=None if healthy else _fetch_logs(v1, pod),
    )


def get_infra_status() -> InfraStatus:
    _load_kube_config()
    v1 = client.CoreV1Api()
    pods = v1.list_pod_for_all_namespaces(watch=False).items
    statuses = sorted(
        (_pod_to_status(p, v1) for p in pods),
        key=lambda s: (s.namespace, s.name),
    )
    overall = all(s.healthy for s in statuses)
    return InfraStatus(overall=overall, pods=statuses)
