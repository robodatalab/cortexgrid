"""Infrastructure status — queries docker on the DGX over TCP."""

from __future__ import annotations

import docker  # type: ignore
from docker.models.containers import Container  # type: ignore
from pydantic import BaseModel

from cortexflow.infra import get_server_ip


class ContainerStatus(BaseModel):
    name: str
    state: str
    health: str
    healthy: bool
    logs: str | None = None


class InfraStatus(BaseModel):
    overall: bool
    containers: list[ContainerStatus]


def _container_to_status(container: Container) -> ContainerStatus:
    attrs = container.attrs["State"]
    state: str = attrs["Status"]
    health: str = attrs.get("Health", {}).get("Status", "none")
    exit_code: int = attrs.get("ExitCode", 0)
    ok = (state == "running" and health in ("healthy", "none")) or (
        state == "exited" and exit_code == 0
    )
    logs = None if ok else container.logs(tail=50).decode("utf-8", errors="replace")
    return ContainerStatus(
        name=container.name or "Anonymous",
        state=state,
        health=health,
        healthy=ok,
        logs=logs,
    )


def get_infra_status() -> InfraStatus:
    client = docker.DockerClient(base_url=f"tcp://{get_server_ip()}:2375")
    containers = client.containers.list(all=True, filters={"name": "robolab-"})
    statuses = [_container_to_status(c) for c in containers]
    overall = all(s.healthy for s in statuses)
    return InfraStatus(overall=overall, containers=statuses)
